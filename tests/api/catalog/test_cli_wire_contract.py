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
        del kwargs
        return CatalogClient(api_base_url, access_token, transport=transport)

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
    config: RootConfig, transport: httpx.ASGITransport, slug: str
) -> dict:
    """Create straight through the real API, bypassing the CLI's own
    create path -- what's under test here is `resource_list`/
    `resource_show`/`resource_versions`, not `capability_create` (already
    covered by the fake-client tests)."""
    client = CatalogClient(config.catalog.api_base_url, 'tok', transport=transport)
    return await client.post(
        catalog.RESOURCES['capability'].api_path,
        {
            'slug': slug,
            'name': slug.title(),
            'description': None,
            'target_metrics': [],
            'owner_id': None,
        },
    )


@pytest.mark.asyncio
async def test_resource_list_wire_contract(cli_catalog_client, rendered) -> None:
    """`resource_list` reads `page['items']`/`['total']`/`['limit']`/
    `['offset']` and sends `lifecycle_state`/`slug`/`limit`/`offset` as
    query params -- both must match what `list_capabilities` actually
    accepts and returns."""
    config = _cli_config()
    created = await _create_capability(config, cli_catalog_client, 'wire-contract-list')

    args = argparse.Namespace(
        lifecycle_state=None, slug='wire-contract-list', limit=50, offset=0
    )
    exit_code = await catalog.resource_list(
        catalog.RESOURCES['capability'], config, args
    )

    assert exit_code == 0
    rows = rendered['table']['rows']
    assert [row['entity_id'] for row in rows] == [created['entity_id']]
    assert rows[0]['slug'] == 'wire-contract-list'


@pytest.mark.asyncio
async def test_resource_show_wire_contract_current_and_versioned(
    cli_catalog_client, rendered
) -> None:
    """`resource_show` GETs `{api_path}/{entity_id}` for the current
    version and `{api_path}/{entity_id}/versions/{version}` when
    `--version` is given -- both must be real routes."""
    config = _cli_config()
    created = await _create_capability(config, cli_catalog_client, 'wire-contract-show')
    entity_id = uuid.UUID(created['entity_id'])

    exit_code = await catalog.resource_show(
        catalog.RESOURCES['capability'],
        config,
        argparse.Namespace(entity_id=entity_id, version=None),
    )
    assert exit_code == 0
    assert rendered['detail']['slug'] == 'wire-contract-show'

    exit_code = await catalog.resource_show(
        catalog.RESOURCES['capability'],
        config,
        argparse.Namespace(entity_id=entity_id, version=1),
    )
    assert exit_code == 0
    assert rendered['detail']['slug'] == 'wire-contract-show'
    assert rendered['detail']['version'] == 1


@pytest.mark.asyncio
async def test_resource_versions_wire_contract(cli_catalog_client, rendered) -> None:
    """`resource_versions` GETs `{api_path}/{entity_id}/versions`, which
    returns a bare list (not the `Page` envelope `list` uses) -- confirm
    the CLI renders that shape without expecting `items`/`total`."""
    config = _cli_config()
    created = await _create_capability(
        config, cli_catalog_client, 'wire-contract-versions'
    )
    entity_id = uuid.UUID(created['entity_id'])

    exit_code = await catalog.resource_versions(
        catalog.RESOURCES['capability'],
        config,
        argparse.Namespace(entity_id=entity_id),
    )
    assert exit_code == 0
    rows = rendered['table']['rows']
    assert [row['entity_id'] for row in rows] == [created['entity_id']]
    assert rows[0]['slug'] == 'wire-contract-versions'
