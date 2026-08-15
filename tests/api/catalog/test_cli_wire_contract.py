"""The CLI's `resource_*` functions never talk to a real Catalog API in
`tests/cli/test_catalog.py` -- that suite monkeypatches `CatalogClient`
entirely, so every query-param name and response envelope key is checked
against *the CLI author's reading* of the routers, never against the
routers themselves. This file closes that gap: it drives the same CLI
functions against the real FastAPI app (via `httpx.ASGITransport`,
in-process, no network) so a mismatched `params` key or a wrong
`page['items']` envelope assumption fails here instead of in a user's
terminal.

Assertions read the data handed to `_print_table`/`_print_detail` rather
than parsing rendered text, since a real terminal's width -- and therefore
`rich`'s column truncation -- is out of scope here; that's a display
concern, not a wire-contract one."""

import argparse
import time
import uuid

import httpx
import pydantic
import pytest

from loom.catalog_client import CatalogClient
from loom.cli import catalog
from loom.config import RootConfig


def _cli_config() -> RootConfig:
    config = RootConfig(config_path='/dev/null')
    config.catalog.api_base_url = 'http://test'
    config.auth.session.access_token = pydantic.SecretStr('irrelevant-under-asgi')
    config.auth.session.expires_at = int(time.time()) + 300
    return config


@pytest.fixture
def cli_catalog_client(api_client, monkeypatch) -> httpx.ASGITransport:
    """Point `loom.cli.catalog.CatalogClient` at the real app used by the
    `api_client` fixture, via the same in-process ASGI transport -- the CLI
    functions under test still construct their own client (as they do in
    production), only the transport is swapped. Returns the transport so
    test setup helpers can talk to the same app through the *real*
    `CatalogClient`, unpatched."""
    transport = httpx.ASGITransport(app=api_client.app)

    def _factory(api_base_url, access_token, **kwargs):
        return CatalogClient(api_base_url, access_token, transport=transport, **kwargs)

    monkeypatch.setattr(catalog, 'CatalogClient', _factory)
    return transport


@pytest.fixture
def rendered(monkeypatch) -> dict:
    """Records what `resource_list`/`resource_show`/`resource_versions`
    hand to the rendering layer, keyed by which one was called -- decouples
    these tests from `rich`'s width-dependent column truncation."""
    calls: dict = {}

    def _print_table(columns, rows):
        calls['table'] = {'columns': columns, 'rows': rows}

    def _print_detail(item):
        calls['detail'] = item

    monkeypatch.setattr(catalog, '_print_table', _print_table)
    monkeypatch.setattr(catalog, '_print_detail', _print_detail)
    return calls


async def _create_capability(
    config: RootConfig,
    transport: httpx.ASGITransport,
    tenant_id: uuid.UUID,
    name: str,
) -> dict:
    """Create straight through the real API, bypassing the CLI's own
    create path -- what's under test here is `resource_list`/
    `resource_show`/`resource_versions`, not `capability_create` (already
    covered by the fake-client tests)."""
    client = CatalogClient(
        config.catalog.api_base_url, 'tok', tenant_id=tenant_id, transport=transport
    )
    return await client.post(
        client.tenant_path(catalog.RESOURCES['capability'].api_path),
        {'name': name, 'description': None, 'target_metrics': []},
    )


@pytest.mark.asyncio
async def test_resource_list_wire_contract(
    cli_catalog_client, rendered, fake_principal
) -> None:
    """`resource_list` reads `page['items']`/`['total']`/`['limit']`/
    `['offset']` and sends `lifecycle_state`/`limit`/`offset` as query
    params -- both must match what `list_capabilities` actually accepts
    and returns."""
    config = _cli_config()
    tenant_id = fake_principal.tenant_id
    created = await _create_capability(
        config, cli_catalog_client, tenant_id, 'Wire Contract List'
    )

    args = argparse.Namespace(
        lifecycle_state=None, limit=50, offset=0, tenant_id=tenant_id
    )
    exit_code = await catalog.resource_list(
        catalog.RESOURCES['capability'], config, args
    )

    assert exit_code == 0
    rows = rendered['table']['rows']
    assert [row['entity_id'] for row in rows] == [created['entity_id']]
    assert rows[0]['name'] == 'Wire Contract List'


@pytest.mark.asyncio
async def test_resource_show_wire_contract_current_and_versioned(
    cli_catalog_client, rendered, fake_principal
) -> None:
    """`resource_show` GETs `{api_path}/{entity_id}` for the current
    version and `{api_path}/{entity_id}/versions/{version}` when
    `--version` is given -- both must be real routes."""
    config = _cli_config()
    tenant_id = fake_principal.tenant_id
    created = await _create_capability(
        config, cli_catalog_client, tenant_id, 'Wire Contract Show'
    )
    entity_id = uuid.UUID(created['entity_id'])

    exit_code = await catalog.resource_show(
        catalog.RESOURCES['capability'],
        config,
        argparse.Namespace(entity_id=entity_id, version=None, tenant_id=tenant_id),
    )
    assert exit_code == 0
    assert rendered['detail']['name'] == 'Wire Contract Show'

    exit_code = await catalog.resource_show(
        catalog.RESOURCES['capability'],
        config,
        argparse.Namespace(entity_id=entity_id, version=1, tenant_id=tenant_id),
    )
    assert exit_code == 0
    assert rendered['detail']['name'] == 'Wire Contract Show'
    assert rendered['detail']['version'] == 1


@pytest.mark.asyncio
async def test_resource_versions_wire_contract(
    cli_catalog_client, rendered, fake_principal
) -> None:
    """`resource_versions` GETs `{api_path}/{entity_id}/versions`, which
    returns a bare list (not the `Page` envelope `list` uses) -- confirm
    the CLI renders that shape without expecting `items`/`total`."""
    config = _cli_config()
    tenant_id = fake_principal.tenant_id
    created = await _create_capability(
        config, cli_catalog_client, tenant_id, 'Wire Contract Versions'
    )
    entity_id = uuid.UUID(created['entity_id'])

    exit_code = await catalog.resource_versions(
        catalog.RESOURCES['capability'],
        config,
        argparse.Namespace(entity_id=entity_id, tenant_id=tenant_id),
    )
    assert exit_code == 0
    rows = rendered['table']['rows']
    assert [row['entity_id'] for row in rows] == [created['entity_id']]
    assert rows[0]['name'] == 'Wire Contract Versions'


@pytest.mark.asyncio
async def test_tenant_crud_wire_contract(cli_catalog_client, rendered) -> None:
    """Tenant isn't a VersionedEntity -- `list` reads the same `Page`
    envelope shape as the trio above, but `show`/`update` hit `{api_path}/
    {id}` directly (no `/versions/...`), and `update` is a real PATCH. Not
    nested under a Tenant path -- it's the one resource this doesn't apply
    to."""
    config = _cli_config()

    exit_code = await catalog.tenant_create(
        config,
        argparse.Namespace(slug='wire-contract-tenant', name='Wire Contract Tenant'),
    )
    assert exit_code == 0
    tenant_id = uuid.UUID(rendered['detail']['id'])

    exit_code = await catalog.tenant_list(
        config, argparse.Namespace(limit=50, offset=0)
    )
    assert exit_code == 0
    assert str(tenant_id) in [row['id'] for row in rendered['table']['rows']]

    exit_code = await catalog.tenant_show(
        config, argparse.Namespace(tenant_id=tenant_id)
    )
    assert exit_code == 0
    assert rendered['detail']['slug'] == 'wire-contract-tenant'

    exit_code = await catalog.tenant_update(
        config, argparse.Namespace(tenant_id=tenant_id, name='Renamed Tenant')
    )
    assert exit_code == 0
    assert rendered['detail']['name'] == 'Renamed Tenant'


@pytest.mark.asyncio
async def test_principal_crud_wire_contract(
    cli_catalog_client, rendered, fake_principal
) -> None:
    """`principal create`/`list`/`show` all nest under `/tenants/
    {tenant_id}/principals` now -- confirms that path actually round-trips
    end to end, not just in the CLI's own argparse."""
    config = _cli_config()
    tenant_id = fake_principal.tenant_id

    exit_code = await catalog.principal_create(
        config,
        argparse.Namespace(
            tenant_id=tenant_id,
            kind='service_account',
            external_id='wire-contract-external-id',
        ),
    )
    assert exit_code == 0
    principal_id = uuid.UUID(rendered['detail']['id'])

    exit_code = await catalog.principal_list(
        config, argparse.Namespace(tenant_id=tenant_id, limit=50, offset=0)
    )
    assert exit_code == 0
    assert str(principal_id) in [row['id'] for row in rendered['table']['rows']]

    exit_code = await catalog.principal_show(
        config,
        argparse.Namespace(principal_id=principal_id, tenant_id=tenant_id),
    )
    assert exit_code == 0
    assert rendered['detail']['external_id'] == 'wire-contract-external-id'
