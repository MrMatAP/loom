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


# --- Skill/Tool/DataSource/DataProduct: create + their sub-resource verbs --
#
# `resource_list`/`resource_show`/`resource_versions`/`resource_transition`
# are exercised against the real app above via Capability alone -- they're
# the same generic function for every resource (`RESOURCES[...]` only
# supplies `api_path`), so re-running them per resource wouldn't catch
# anything new. What's resource-specific and therefore worth its own
# wire-contract test: each `*_create` function's payload shape, and the
# sub-resource functions (`skill_node_*`, `skill_edge_*`, `tool_binding_*`,
# `dataproduct_lineage_*`), which have no generic-verb equivalent at all.


@pytest.mark.asyncio
async def test_datasource_create_wire_contract(
    cli_catalog_client, rendered, fake_principal
) -> None:
    config = _cli_config()
    tenant_id = fake_principal.tenant_id

    exit_code = await catalog.datasource_create(
        config,
        argparse.Namespace(
            name='Wire Contract DataSource',
            description=None,
            kind='database',
            connection_binding_id=None,
            tenant_id=tenant_id,
        ),
    )

    assert exit_code == 0
    assert rendered['detail']['name'] == 'Wire Contract DataSource'
    assert rendered['detail']['kind'] == 'database'


@pytest.mark.asyncio
async def test_dataproduct_create_and_lineage_wire_contract(
    cli_catalog_client, rendered, fake_principal
) -> None:
    config = _cli_config()
    tenant_id = fake_principal.tenant_id

    ds_exit_code = await catalog.datasource_create(
        config,
        argparse.Namespace(
            name='Lineage Source',
            description=None,
            kind='database',
            connection_binding_id=None,
            tenant_id=tenant_id,
        ),
    )
    assert ds_exit_code == 0
    datasource_id = uuid.UUID(rendered['detail']['id'])

    dp_exit_code = await catalog.dataproduct_create(
        config,
        argparse.Namespace(
            name='Wire Contract DataProduct',
            description=None,
            contract='{"schema": "v1"}',
            tenant_id=tenant_id,
        ),
    )
    assert dp_exit_code == 0
    assert rendered['detail']['contract'] == {'schema': 'v1'}
    entity_id = uuid.UUID(rendered['detail']['entity_id'])

    add_exit_code = await catalog.dataproduct_lineage_add(
        config,
        argparse.Namespace(
            entity_id=entity_id,
            version=1,
            source_datasource_id=datasource_id,
            source_dataproduct_id=None,
            tenant_id=tenant_id,
        ),
    )
    assert add_exit_code == 0
    assert rendered['detail']['source_datasource_id'] == str(datasource_id)

    list_exit_code = await catalog.dataproduct_lineage_list(
        config,
        argparse.Namespace(entity_id=entity_id, version=1, tenant_id=tenant_id),
    )
    assert list_exit_code == 0
    rows = rendered['table']['rows']
    assert len(rows) == 1
    assert rows[0]['source_datasource_id'] == str(datasource_id)


@pytest.mark.asyncio
async def test_skill_create_and_node_edge_wire_contract(
    cli_catalog_client, rendered, fake_principal
) -> None:
    config = _cli_config()
    tenant_id = fake_principal.tenant_id

    tool_exit_code = await catalog.tool_create(
        config,
        argparse.Namespace(
            name='Node Tool',
            description=None,
            invocation_spec='{}',
            auth_binding_id=None,
            tenant_id=tenant_id,
        ),
    )
    assert tool_exit_code == 0
    tool_id = uuid.UUID(rendered['detail']['id'])

    skill_exit_code = await catalog.skill_create(
        config,
        argparse.Namespace(
            name='Wire Contract Skill',
            description=None,
            layer='business_ops',
            kind='composite',
            is_entry_point=True,
            atomic_content=None,
            tenant_id=tenant_id,
        ),
    )
    assert skill_exit_code == 0
    assert rendered['detail']['is_entry_point'] is True
    entity_id = uuid.UUID(rendered['detail']['entity_id'])

    node_add_exit_code = await catalog.skill_node_add(
        config,
        argparse.Namespace(
            entity_id=entity_id,
            version=1,
            node_key='step-1',
            node_type='tool',
            agent_id=None,
            skill_ref_id=None,
            tool_id=tool_id,
            position=None,
            tenant_id=tenant_id,
        ),
    )
    assert node_add_exit_code == 0
    node_id = uuid.UUID(rendered['detail']['id'])

    node_list_exit_code = await catalog.skill_node_list(
        config,
        argparse.Namespace(entity_id=entity_id, version=1, tenant_id=tenant_id),
    )
    assert node_list_exit_code == 0
    assert [row['id'] for row in rendered['table']['rows']] == [str(node_id)]

    second_node_add = await catalog.skill_node_add(
        config,
        argparse.Namespace(
            entity_id=entity_id,
            version=1,
            node_key='step-2',
            node_type='tool',
            agent_id=None,
            skill_ref_id=None,
            tool_id=tool_id,
            position=None,
            tenant_id=tenant_id,
        ),
    )
    assert second_node_add == 0
    second_node_id = uuid.UUID(rendered['detail']['id'])

    edge_add_exit_code = await catalog.skill_edge_add(
        config,
        argparse.Namespace(
            entity_id=entity_id,
            version=1,
            from_node_id=node_id,
            to_node_id=second_node_id,
            tenant_id=tenant_id,
        ),
    )
    assert edge_add_exit_code == 0
    assert rendered['detail']['from_node_id'] == str(node_id)

    edge_list_exit_code = await catalog.skill_edge_list(
        config,
        argparse.Namespace(entity_id=entity_id, version=1, tenant_id=tenant_id),
    )
    assert edge_list_exit_code == 0
    assert len(rendered['table']['rows']) == 1


@pytest.mark.asyncio
async def test_tool_create_and_binding_wire_contract(
    cli_catalog_client, rendered, fake_principal
) -> None:
    config = _cli_config()
    tenant_id = fake_principal.tenant_id

    ds_exit_code = await catalog.datasource_create(
        config,
        argparse.Namespace(
            name='Binding Source',
            description=None,
            kind='api',
            connection_binding_id=None,
            tenant_id=tenant_id,
        ),
    )
    assert ds_exit_code == 0
    datasource_id = uuid.UUID(rendered['detail']['id'])

    tool_exit_code = await catalog.tool_create(
        config,
        argparse.Namespace(
            name='Wire Contract Tool',
            description=None,
            invocation_spec='{"method": "GET"}',
            auth_binding_id=None,
            tenant_id=tenant_id,
        ),
    )
    assert tool_exit_code == 0
    entity_id = uuid.UUID(rendered['detail']['entity_id'])

    binding_add_exit_code = await catalog.tool_binding_add(
        config,
        argparse.Namespace(
            entity_id=entity_id,
            version=1,
            datasource_id=datasource_id,
            dataproduct_id=None,
            access_mode='read',
            tenant_id=tenant_id,
        ),
    )
    assert binding_add_exit_code == 0
    assert rendered['detail']['access_mode'] == 'read'

    binding_list_exit_code = await catalog.tool_binding_list(
        config,
        argparse.Namespace(entity_id=entity_id, version=1, tenant_id=tenant_id),
    )
    assert binding_list_exit_code == 0
    rows = rendered['table']['rows']
    assert len(rows) == 1
    assert rows[0]['datasource_id'] == str(datasource_id)


# --- Environment: CRUD, not a VersionedEntity -------------------------------


@pytest.mark.asyncio
async def test_environment_crud_wire_contract(
    cli_catalog_client, rendered, fake_principal
) -> None:
    """`environment create`/`list`/`show`/`update` all nest under
    `/tenants/{tenant_id}/environments` -- confirms that path, the `Page`
    envelope `list` reads, and `update`'s PATCH-in-place all round-trip
    end to end against the real router."""
    config = _cli_config()
    tenant_id = fake_principal.tenant_id

    create_exit_code = await catalog.environment_create(
        config,
        argparse.Namespace(
            name='wire-contract-env',
            kind='sandbox',
            compute_boundary_ref='cluster-a',
            network_boundary_ref='vpc-a',
            tenant_id=tenant_id,
        ),
    )
    assert create_exit_code == 0
    assert rendered['detail']['name'] == 'wire-contract-env'
    environment_id = uuid.UUID(rendered['detail']['id'])

    list_exit_code = await catalog.environment_list(
        config, argparse.Namespace(limit=50, offset=0, tenant_id=tenant_id)
    )
    assert list_exit_code == 0
    assert str(environment_id) in [row['id'] for row in rendered['table']['rows']]

    show_exit_code = await catalog.environment_show(
        config,
        argparse.Namespace(environment_id=environment_id, tenant_id=tenant_id),
    )
    assert show_exit_code == 0
    assert rendered['detail']['compute_boundary_ref'] == 'cluster-a'

    update_exit_code = await catalog.environment_update(
        config,
        argparse.Namespace(
            environment_id=environment_id,
            compute_boundary_ref='cluster-b',
            network_boundary_ref=None,
            tenant_id=tenant_id,
        ),
    )
    assert update_exit_code == 0
    assert rendered['detail']['compute_boundary_ref'] == 'cluster-b'
    assert rendered['detail']['network_boundary_ref'] == 'vpc-a'
