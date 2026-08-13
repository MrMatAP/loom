import argparse
import importlib.resources

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from loom.config import RootConfig
from loom.model import (  # noqa: F401  (imported for side effect: table registration)
    agent,
    capability,
    dataproduct,
    datasource,
    environment,
    evaluation,
    model_endpoint,
    observability,
    skill,
    tool,
)
from loom.model.engine import get_session_factory
from loom.model.enums import AuditDecision, PrincipalKind
from loom.model.governance import AuditEvent
from loom.model.tenant import Principal, Tenant


def _alembic_config(root_config: RootConfig) -> Config:
    """Build an Alembic Config for the packaged migrations and configured DSN."""
    script_location = importlib.resources.files('loom') / 'migrations'
    config = Config()
    config.set_main_option('script_location', str(script_location))
    config.set_main_option('sqlalchemy.url', root_config.database.dsn)
    return config


async def db_upgrade(config: RootConfig, args: argparse.Namespace) -> int:
    command.upgrade(_alembic_config(config), getattr(args, 'revision', None) or 'head')
    return 0


async def db_downgrade(config: RootConfig, args: argparse.Namespace) -> int:
    command.downgrade(_alembic_config(config), args.revision)
    return 0


async def db_current(config: RootConfig, args: argparse.Namespace) -> int:
    del args
    command.current(_alembic_config(config))
    return 0


async def db_history(config: RootConfig, args: argparse.Namespace) -> int:
    del args
    command.history(_alembic_config(config))
    return 0


async def db_revision(config: RootConfig, args: argparse.Namespace) -> int:
    command.revision(
        _alembic_config(config), message=args.message, autogenerate=args.autogenerate
    )
    return 0


async def db_seed_principal(config: RootConfig, args: argparse.Namespace) -> int:
    """Bootstrap a Tenant/Principal by writing directly to the database via
    `config.database` -- bypasses the Catalog API/Policy Engine/RBAC
    entirely. This is the only way to create the *first* Principal in a
    fresh deployment: `loom principal create` (and the `POST /principals`
    it calls) both require an already-authorized, already-provisioned
    caller, which by definition doesn't exist yet. Every Principal after
    this first one should go through `loom principal create` instead, so
    it's actually policy-checked and audit-logged the normal way -- this
    command still writes one `AuditEvent` (`actor_principal_id` pointing at
    the Principal it just created, since there's no other actor to
    attribute a bootstrap to) so the bypass itself isn't silent.

    Reuses an existing Tenant by slug rather than erroring, and reports
    (rather than hitting the unique-constraint violation) if a Principal
    for this (tenant, external_id) already exists -- safe to re-run."""
    session_factory = get_session_factory(config.database)
    with session_factory() as session:
        tenant = session.scalar(
            sa.select(Tenant).where(Tenant.slug == args.tenant_slug)
        )
        if tenant is None:
            if not args.tenant_name:
                print(
                    f'No Tenant with slug {args.tenant_slug!r} exists yet; '
                    '--tenant-name is required to create one.'
                )
                return 1
            tenant = Tenant(slug=args.tenant_slug, name=args.tenant_name)
            session.add(tenant)
            session.flush()
            print(f'Created Tenant {tenant.slug!r} ({tenant.id}).')
        else:
            print(f'Reusing existing Tenant {tenant.slug!r} ({tenant.id}).')

        existing = session.scalar(
            sa.select(Principal).where(
                Principal.tenant_id == tenant.id,
                Principal.external_id == args.external_id,
            )
        )
        if existing is not None:
            print(
                f'A Principal for external_id={args.external_id!r} already exists '
                f'in this Tenant: {existing.id}. Nothing to do.'
            )
            return 0

        principal = Principal(
            tenant_id=tenant.id,
            kind=PrincipalKind(args.kind),
            display_name=args.display_name,
            external_id=args.external_id,
        )
        session.add(principal)
        session.flush()
        session.add(
            AuditEvent(
                tenant_id=tenant.id,
                actor_principal_id=principal.id,
                action='cli_bootstrap_seed_principal',
                entity_type='principal',
                entity_id=principal.id,
                decision=AuditDecision.ALLOW,
                details={
                    'source': 'loom db seed-principal',
                    'note': 'bypassed the Policy Engine -- direct DB bootstrap',
                },
            )
        )
        session.commit()

        print(
            f'Created Principal {principal.id} '
            f'(external_id={principal.external_id!r}, kind={principal.kind.value}) '
            f'in Tenant {tenant.slug!r}.'
        )
        print(
            'This only creates the database record. The matching IDP account '
            'still needs a `tenant_id` attribute set to '
            f"{tenant.id} (see docs/admin-guide.md) or its tokens won't carry "
            'the claim this Principal is resolved by.'
        )
        return 0
