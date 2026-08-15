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
from loom.model.tenant import Tenant


def _alembic_config(root_config: RootConfig) -> Config:
    """Build an Alembic Config for the packaged migrations and configured DSN."""
    script_location = importlib.resources.files('loom') / 'migrations'
    config = Config()
    config.set_main_option('script_location', str(script_location))
    config.set_main_option('sqlalchemy.url', root_config.database.dsn)
    return config


def _seed_default_tenant(config: RootConfig) -> tuple[str, str] | None:
    """If no Tenant exists yet, create one -- most deployments only ever
    need a single Tenant, and this saves the platform administrator an
    explicit `loom tenant create` step for that common case (see
    docs/admin-guide.md's "Platform administrator" section). A no-op, not
    an error, once any Tenant exists -- including on a re-run of `loom db
    upgrade` -- so it's always safe to call unconditionally.

    Unlike `POST /tenants`/`POST /principals` (see `audit.py`), this
    writes no `AuditEvent`: `db upgrade` runs before any login, so there's
    no token/`sub` to attribute the write to, and unlike those API calls
    the row itself grants no identity access by itself -- it's just an
    empty administrative record until a Principal is provisioned into it,
    which *is* audited. That's a materially lower-risk write than the
    `db seed-principal` path this design replaced, which is why it's
    exempt from the "no unaudited bootstrap path" rule that killed that
    command.

    Plain sync `Session`, not the async engine the API server uses: this
    runs inline with Alembic's own (sync) migration step, right after it,
    so it needs no extra async-driver DSN handling of its own. Returns
    (slug, id) of the created Tenant, or None if one already existed (or
    a concurrent/prior run created `default` between the check above and
    this insert -- `tenant.slug` is globally unique, so that race just
    loses gracefully rather than aborting the migration that already
    succeeded)."""
    session_factory = get_session_factory(config.database)
    with session_factory() as session:
        if session.scalar(sa.select(Tenant.id).limit(1)) is not None:
            return None
        tenant = Tenant(slug='default', name='Default')
        session.add(tenant)
        try:
            session.flush()
        except sa.exc.IntegrityError:
            session.rollback()
            return None
        result = (tenant.slug, str(tenant.id))
        session.commit()
        return result


async def db_upgrade(config: RootConfig, args: argparse.Namespace) -> int:
    command.upgrade(_alembic_config(config), getattr(args, 'revision', None) or 'head')
    created = _seed_default_tenant(config)
    if created is not None:
        slug, tenant_id = created
        print(f"Created default Tenant '{slug}' ({tenant_id}).")
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
