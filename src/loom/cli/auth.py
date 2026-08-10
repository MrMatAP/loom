import argparse
import time

import pydantic

from loom.config import RootConfig
from loom.idp.device_flow import DeviceCodeClient, DeviceCodeError


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
            '`loom idp register-cli-client` first.'
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
    config.save()
    print('Logged in.')
    return 0


async def auth_logout(config: RootConfig, args: argparse.Namespace) -> int:
    """Clear the cached CLI session."""
    del args
    config.auth.session.access_token = None
    config.auth.session.refresh_token = None
    config.auth.session.expires_at = None
    config.save()
    print('Logged out.')
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
