"""`loom-admin operator`: manage Platform Operators -- the accounts that may
create Tenants and provision Principals. An Operator is an IdP account holding
the `platform-operator` and `catalog-maintainer` client roles *and* a
Principal in at least one Tenant, so `grant` does both."""

import argparse
import uuid

import sqlalchemy as sa

from loom.cli.idp import add_admin_login_args, login_as_admin, resolve_issuer_url
from loom.config import AdminConfig
from loom.domain.enums import AuditDecision, PrincipalKind
from loom.persistence.engine import get_session_factory
from loom.persistence.governance import AuditEvent
from loom.persistence.tenant import Principal, Tenant

# The roles that make someone a Platform Operator: tenancy and identity
# management, plus full content access (revocable independently -- drop
# `catalog-maintainer` from the user in the IdP).
OPERATOR_ROLES = ['platform-operator', 'catalog-maintainer']

DEFAULT_TENANT_SLUG = 'default'


def _find_tenant(session, ref: str | None) -> Tenant | None:
    """`ref` is a Tenant slug or id; unset means the `default` Tenant."""
    if ref is None:
        return session.scalar(
            sa.select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)
        )
    try:
        by_id = session.get(Tenant, uuid.UUID(ref))
    except ValueError:
        by_id = None
    return by_id or session.scalar(sa.select(Tenant).where(Tenant.slug == ref))


def _ensure_principal(config: AdminConfig, tenant_ref: str | None, sub: str) -> str:
    """Create a user Principal for `sub` in the Tenant unless one exists
    (idempotent). Written straight to the database -- `loom-admin` has no
    API login -- with the same `principal.create` AuditEvent the API writes,
    attributed to `loom-admin` since there's no Principal to attribute to.
    Returns a one-line description of what happened; raises LookupError if
    the Tenant doesn't exist."""
    session_factory = get_session_factory(config.database)
    with session_factory() as session:
        tenant = _find_tenant(session, tenant_ref)
        if tenant is None:
            raise LookupError(
                f'No Tenant {tenant_ref!r} exists.'
                if tenant_ref
                else f"No '{DEFAULT_TENANT_SLUG}' Tenant exists; run `loom-admin db upgrade`."
            )
        existing = session.scalar(
            sa.select(Principal).where(
                Principal.tenant_id == tenant.id, Principal.external_id == sub
            )
        )
        if existing is not None:
            return f"already has a Principal in Tenant '{tenant.slug}' ({existing.id})"
        principal = Principal(
            tenant_id=tenant.id, kind=PrincipalKind.USER, external_id=sub
        )
        session.add(principal)
        session.flush()
        session.add(
            AuditEvent(
                tenant_id=tenant.id,
                action='principal.create',
                entity_type='principal',
                entity_id=principal.id,
                decision=AuditDecision.ALLOW,
                details={'actor_external_id': 'loom-admin'},
            )
        )
        session.commit()
        return f"created Principal {principal.id} in Tenant '{tenant.slug}'"


async def operator_grant(config: AdminConfig, args: argparse.Namespace) -> int:
    """Make an existing IdP account a Platform Operator and give them a
    Principal in a Tenant (the `default` one unless `--tenant`)."""
    api_client_id = config.auth.audience
    if not api_client_id:
        print('config.auth.audience is unset; run `loom-admin idp register` first.')
        return 1

    # Check the Tenant before touching the IdP, so a typo doesn't leave the
    # roles granted but no Principal.
    session_factory = get_session_factory(config.database)
    with session_factory() as session:
        if _find_tenant(session, args.tenant) is None:
            print(
                f'No Tenant {args.tenant!r} exists.'
                if args.tenant
                else f"No '{DEFAULT_TENANT_SLUG}' Tenant exists; run `loom-admin db upgrade`."
            )
            return 1

    issuer_url = resolve_issuer_url(config, args)
    if not issuer_url:
        return 1
    client = await login_as_admin(issuer_url, args)
    user_id = await client.grant_client_roles(
        username=args.username, client_id=api_client_id, role_names=OPERATOR_ROLES
    )
    if user_id is None:
        print(f'No user {args.username!r} exists in the IdP; create the account first.')
        return 1
    print(f'Granted {", ".join(OPERATOR_ROLES)} to {args.username} (sub: {user_id}).')

    print(f'{args.username}: {_ensure_principal(config, args.tenant, user_id)}.')
    print('Next, as that user: `loom auth login --server <url>`.')
    return 0


async def operator_list(config: AdminConfig, args: argparse.Namespace) -> int:
    """List the IdP accounts holding the Operator roles, with the Tenants
    they have a Principal in."""
    api_client_id = config.auth.audience
    if not api_client_id:
        print('config.auth.audience is unset; run `loom-admin idp register` first.')
        return 1
    issuer_url = resolve_issuer_url(config, args)
    if not issuer_url:
        return 1
    client = await login_as_admin(issuer_url, args)

    operators: dict[str, tuple[str, set[str]]] = {}
    for role in OPERATOR_ROLES:
        for member in await client.list_client_role_members(
            client_id=api_client_id, role_name=role
        ):
            operators.setdefault(member.user_id, (member.username, set()))[1].add(role)
    if not operators:
        print('No Platform Operators.')
        return 0

    session_factory = get_session_factory(config.database)
    with session_factory() as session:
        rows = session.execute(
            sa.select(Principal.external_id, Tenant.slug)
            .join(Tenant, Tenant.id == Principal.tenant_id)
            .where(Principal.external_id.in_(operators))
        ).all()
    tenants: dict[str, list[str]] = {}
    for external_id, slug in rows:
        tenants.setdefault(external_id, []).append(slug)

    print(f'{"USERNAME":<24} {"SUB":<38} {"ROLES":<44} TENANTS')
    for sub, (username, roles) in sorted(operators.items(), key=lambda kv: kv[1][0]):
        missing = [r for r in OPERATOR_ROLES if r not in roles]
        role_text = ','.join(r for r in OPERATOR_ROLES if r in roles)
        if missing:
            role_text += f' (missing {",".join(missing)})'
        tenant_text = ','.join(sorted(tenants.get(sub, []))) or '-'
        print(f'{username:<24} {sub:<38} {role_text:<44} {tenant_text}')
    return 0


def add_operator_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register the `operator` command group."""
    parser = subparsers.add_parser('operator', help='Platform Operator management')
    sub = parser.add_subparsers(required=True)

    grant = sub.add_parser(
        'grant',
        help=(
            'Make an existing IdP account a Platform Operator '
            f'({" and ".join(OPERATOR_ROLES)}) and create their Principal '
            'in a Tenant'
        ),
    )
    add_admin_login_args(grant)
    grant.add_argument('username', help='IdP username of the account')
    grant.add_argument(
        '--tenant',
        default=None,
        metavar='SLUG_OR_ID',
        help=f"Tenant to create the Principal in, defaults to '{DEFAULT_TENANT_SLUG}'",
    )
    grant.set_defaults(func=operator_grant)

    list_parser = sub.add_parser(
        'list', help='List Platform Operators and the Tenants they belong to'
    )
    add_admin_login_args(list_parser)
    list_parser.set_defaults(func=operator_list)
