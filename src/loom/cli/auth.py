import argparse
import time
import uuid

import httpx
import jwt
import pydantic

from loom.catalog_client import CatalogApiError, CatalogClient
from loom.config import RootConfig
from loom.idp.device_flow import DeviceCodeClient, DeviceCodeError

# Claims worth calling out by name in `whoami` -- everything else in the
# token still gets printed, just without a caption. `sub` is first since
# it's what `get_principal_in_tenant`
# (`src/loom/api/catalog/dependencies.py`) looks a Principal up by -- a
# missing/unprovisioned Principal for `sub` is
# the most common cause of a confusing 401, and this command exists
# specifically so that's visible without hand-decoding a JWT. `name` is
# what a Principal's display name is read from at request time (there's no
# stored display_name -- see `src/loom/model/tenant.py`'s `Principal`
# docstring).
_HEADLINE_CLAIMS = (
    'sub',
    'name',
    'preferred_username',
    'email',
    'scope',
    'roles',
    'iss',
    'aud',
    'azp',
)


def decode_cached_claims(config: RootConfig) -> dict | None:
    """The cached access token's claims, decoded without verifying the
    signature -- or None if there's no cached token at all.

    Deliberately unverified: this only ever shows a user their own cached
    token back to themselves (`whoami`), never a trust/authorization
    decision -- that stays server-side in `TokenValidator.decode`, which
    does verify against the JWKS. No network call, works offline, and
    still works to inspect *why* the server is rejecting the token (e.g. it
    expired, or is missing a claim) -- exactly the case a verifying decode
    can't help with."""
    if config.auth.session.access_token is None:
        return None
    token = config.auth.session.access_token.get_secret_value()
    return jwt.decode(token, options={'verify_signature': False})


async def auth_login(config: RootConfig, args: argparse.Namespace) -> int:
    """Log in interactively via the IDP's OAuth2 Device Authorization Grant.

    The resulting access/refresh tokens are cached in the local config so
    later authenticated commands (e.g. `loom capability create`) can reuse
    the session without logging in again.
    """
    issuer_url = args.issuer_url or config.auth.issuer
    if not issuer_url:
        print('No --issuer-url given and config.auth.issuer is unset.')
        return 1
    client_id = args.client_id or config.auth.cli_client_id
    if not client_id:
        print(
            'No --client-id given and config.auth.cli_client_id is unset; run '
            '`loom idp register` first.'
        )
        return 1

    device_client = DeviceCodeClient(issuer_url, client_id)
    authorization = await device_client.start()
    if authorization.verification_uri_complete:
        print(f'Open {authorization.verification_uri_complete} to log in.')
    else:
        print(
            f'Open {authorization.verification_uri} and enter code '
            f'{authorization.user_code} to log in.'
        )

    try:
        tokens = await device_client.poll(authorization)
    except DeviceCodeError as exc:
        print(f'Login failed: {exc}')
        return 1

    config.auth.session.access_token = pydantic.SecretStr(tokens.access_token)
    config.auth.session.refresh_token = (
        pydantic.SecretStr(tokens.refresh_token) if tokens.refresh_token else None
    )
    config.auth.session.expires_at = int(time.time()) + tokens.expires_in
    # A fresh login may resolve to a different identity than whichever one
    # last selected a Tenant (see `auth_logout`) -- don't carry a stale
    # selection over onto a new identity's session.
    config.auth.session.tenant_id = None
    config.save()

    await _select_tenant(config, tokens.access_token)

    print('Logged in.')
    return 0


async def _select_tenant(config: RootConfig, access_token: str) -> None:
    """Resolve `config.auth.session.tenant_id` right after a fresh login,
    instead of leaving it unset until either an ambiguous request 401s or
    someone remembers to run `loom auth set-tenant` -- see
    `GET /tenants/mine` (`src/loom/api/catalog/tenant/router.py`) for what
    "available" means here: every Tenant for a platform admin, else only
    the Tenants this identity already has a Principal in.

    Never fails the login itself -- a Tenant can always be picked
    afterwards via `loom auth set-tenant`, and a login that already holds
    valid tokens shouldn't be thrown away just because this best-effort
    step couldn't reach the API or the caller declined to choose."""
    client = CatalogClient(config.catalog.api_base_url, access_token)
    try:
        page = await client.get('/api/v1/tenants/mine')
    except (CatalogApiError, httpx.HTTPError) as exc:
        print(
            f'Could not list available Tenants ({exc}); run `loom auth '
            'set-tenant <tenant_id>` once you know which to use.'
        )
        return

    tenants = page['items']
    if not tenants:
        print(
            'No Tenant is available to this identity yet -- ask your admin to '
            'run `loom principal create` for you (see docs/admin-guide.md).'
        )
        return

    if len(tenants) == 1:
        tenant = tenants[0]
        config.auth.session.tenant_id = uuid.UUID(tenant['id'])
        config.save()
        print(
            f"Selected Tenant '{tenant['slug']}' ({tenant['id']}) -- the only "
            'one available.'
        )
        return

    print('Multiple Tenants are available to this identity -- choose one:')
    for index, tenant in enumerate(tenants, start=1):
        print(f'  {index}. {tenant["slug"]} -- {tenant["name"]} ({tenant["id"]})')
    while True:
        try:
            choice = input(f'Select a Tenant [1-{len(tenants)}]: ').strip()
        except EOFError, KeyboardInterrupt:
            print(
                '\nNo Tenant selected; run `loom auth set-tenant <tenant_id>` '
                'once you know which to use.'
            )
            return
        if choice.isdigit() and 1 <= int(choice) <= len(tenants):
            tenant = tenants[int(choice) - 1]
            config.auth.session.tenant_id = uuid.UUID(tenant['id'])
            config.save()
            print(f"Selected Tenant '{tenant['slug']}' ({tenant['id']}).")
            return
        print(f'Invalid selection {choice!r}, try again.')


async def auth_logout(config: RootConfig, args: argparse.Namespace) -> int:
    """Clear the cached CLI session, including any locally-selected Tenant
    (see `auth_set_tenant`) -- a fresh login might resolve to a different
    identity, so a stale selection shouldn't carry over silently."""
    del args
    config.auth.session.access_token = None
    config.auth.session.refresh_token = None
    config.auth.session.expires_at = None
    config.auth.session.tenant_id = None
    config.save()
    print('Logged out.')
    return 0


async def auth_set_tenant(config: RootConfig, args: argparse.Namespace) -> int:
    """Set, clear, or show the locally-selected Tenant used when this
    identity is provisioned in more than one Tenant (see
    docs/admin-guide.md's "How a caller's Tenant is resolved" section).

    There's no `tenant_id` claim on any token for the CLI to read this
    from, so it's tracked purely client-side (`config.auth.session.
    tenant_id`) and embedded directly into the URL path of every
    subsequent request (`CatalogClient.tenant_path`, e.g.
    `/api/v1/tenants/{tenant_id}/capabilities`) -- `get_current_principal`
    only ever resolves the caller's Principal within *that* Tenant, never
    granting access to one it isn't otherwise provisioned in, so an
    incorrect selection just 403s rather than leaking anything. `--tenant-
    id` on individual commands overrides this selection for just that
    call; has no effect (and isn't needed) for an identity provisioned in
    only one Tenant."""
    if args.clear:
        config.auth.session.tenant_id = None
        config.save()
        print('Cleared the locally-selected Tenant.')
        return 0
    if args.tenant_id is None:
        current = config.auth.session.tenant_id
        print(str(current) if current is not None else 'No Tenant selected.')
        return 0
    config.auth.session.tenant_id = args.tenant_id
    config.save()
    print(f'Selected Tenant {args.tenant_id} for subsequent requests.')
    return 0


async def auth_status(config: RootConfig, args: argparse.Namespace) -> int:
    """Report whether the CLI has a live cached session."""
    del args
    if config.auth.session.access_token is None:
        print('Not logged in. Run `loom auth login`.')
        return 1
    remaining = (config.auth.session.expires_at or 0) - int(time.time())
    if remaining <= 0:
        print('Session expired. Run `loom auth login`.')
        return 1
    print(f'Logged in, session valid for another {remaining}s.')
    return 0


async def auth_whoami(config: RootConfig, args: argparse.Namespace) -> int:
    """Print the cached access token's claims -- who the server thinks you
    are, not just whether the CLI thinks you're logged in (that's `status`).
    Existed as a gap: diagnosing "why does the API 401/403 me" previously
    meant decoding the token by hand; this surfaces exactly the claims
    `get_principal_in_tenant`/`require_scopes` actually check, most
    importantly `sub`, whose absence of a matching Principal is the most
    common cause of a confusing 401/403 (see docs/admin-guide.md's
    Troubleshooting section)."""
    del args
    claims = decode_cached_claims(config)
    if claims is None:
        print('Not logged in. Run `loom auth login`.')
        return 1

    width = max((len(k) for k in _HEADLINE_CLAIMS), default=0)
    for key in _HEADLINE_CLAIMS:
        print(f'{key:<{width}} : {claims.get(key, "-")}')

    other_keys = sorted(set(claims) - set(_HEADLINE_CLAIMS))
    if other_keys:
        print()
        print('other claims:')
        width = max(len(k) for k in other_keys)
        for key in other_keys:
            print(f'  {key:<{width}} : {claims[key]}')

    print()
    selected_tenant = config.auth.session.tenant_id
    if selected_tenant is not None:
        print(
            f'locally-selected tenant : {selected_tenant} (see `loom auth set-tenant`)'
        )
    else:
        print(
            'locally-selected tenant : none (only needed if this identity is '
            'provisioned in more than one Tenant -- see `loom auth set-tenant`)'
        )

    return 0
