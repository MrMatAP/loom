import argparse
import dataclasses
import functools
import json
import pathlib
import time
import typing
import uuid

import rich.box
import rich.console
import rich.table

from loom.catalog_client import CatalogApiError, CatalogClient
from loom.config import RootConfig
from loom.domain.enums import (
    DataBindingAccessMode,
    DataSourceKind,
    EnvironmentKind,
    GraphNodeType,
    Layer,
    LifecycleState,
    MemoryScope,
    ModelProtocol,
    PrincipalKind,
    SkillKind,
)

console = rich.console.Console()


# --- Shared plumbing ---------------------------------------------------


def _access_token(config: RootConfig) -> str | None:
    """The cached CLI session's access token, or None (after printing why)
    if there isn't a usable one -- the same "not logged in"/"expired" story
    as `loom auth status`, so a command fails with an actionable message
    instead of an opaque 401 from the API."""
    if config.auth.session.access_token is None:
        console.print('Not logged in. Run [bold]loom auth login[/bold].')
        return None
    remaining = (config.auth.session.expires_at or 0) - int(time.time())
    if remaining <= 0:
        console.print('Session expired. Run [bold]loom auth login[/bold].')
        return None
    return config.auth.session.access_token.get_secret_value()


def _parse_json(value: str, flag: str, expected: type) -> typing.Any:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f'{flag}: invalid JSON: {exc}') from exc
    if not isinstance(parsed, expected):
        raise TypeError(f'{flag}: must be a JSON {expected.__name__}')
    return parsed


def _uuid_str(value: typing.Any) -> str | None:
    return str(value) if value is not None else None


def _print_api_error(exc: CatalogApiError) -> int:
    if exc.status_code == 401:
        console.print(f'[red]Not authorized:[/red] {exc.detail}')
        console.print(
            'If that mentions an expired/invalid token, run `loom auth '
            'login` again. Otherwise (e.g. no Principal record for your '
            'account), logging in again will not help -- it is a '
            'server-side provisioning issue for your admin to fix.'
        )
    elif exc.status_code == 403:
        console.print(f'[red]Forbidden:[/red] {exc.detail}')
        console.print(
            'This usually means the Tenant in the request path is not one '
            'you are provisioned in -- check `--tenant-id`/`loom auth '
            'set-tenant`, or ask your admin to run `loom principal create` '
            'for you in that Tenant. Re-running `loom auth login` will not '
            'help if that is the cause.'
        )
    else:
        console.print(f'[red]{exc.status_code}[/red] {exc.detail}')
    return 1


def _format_cell(value: typing.Any) -> str:
    """Render one field for either table style below: `None` as a dash
    rather than the literal string "None", dict/list fields (target_metrics,
    llm_config, permission_boundary, ...) as compact JSON rather than a
    Python repr."""
    if value is None:
        return '-'
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(',', ':'))
    return str(value)


def _print_table(columns: tuple[str, ...], rows: list[dict]) -> None:
    """An `openstack list`-style table: one row per item, columns fixed
    per resource."""
    table = rich.table.Table(box=rich.box.SIMPLE_HEAVY, header_style='bold cyan')
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*(_format_cell(row.get(column)) for column in columns))
    console.print(table)


def _print_detail(item: dict) -> None:
    """An `openstack show`-style two-column Field/Value table for a
    single entity (create/update/show/transition results)."""
    table = rich.table.Table(box=rich.box.SIMPLE_HEAVY, show_header=False)
    table.add_column('Field', style='bold cyan', no_wrap=True)
    table.add_column('Value')
    for key, value in item.items():
        table.add_row(key, _format_cell(value))
    console.print(table)


def _resolve_tenant_id(
    config: RootConfig, args: argparse.Namespace
) -> uuid.UUID | None:
    """The Tenant a resource command targets -- every resource but Tenant
    itself is nested under one (`/api/v1/tenants/{tenant_id}/...`), since
    the caller's identity may hold a Principal in more than one and the
    server resolves access strictly against whichever Tenant is named in
    the URL. `--tenant-id` on the command overrides the locally-selected
    one (`loom auth set-tenant`, normally set automatically by `loom auth
    login`) when given; prints an actionable message and returns None if
    neither is available."""
    tenant_id = getattr(args, 'tenant_id', None) or config.auth.session.tenant_id
    if tenant_id is None:
        console.print(
            'No --tenant-id given and no Tenant is locally selected. Run '
            '[bold]loom auth set-tenant <tenant_id>[/bold] or pass '
            '--tenant-id explicitly.'
        )
    return tenant_id


async def _post_and_show(
    config: RootConfig, tenant_id: uuid.UUID, suffix: str, payload: dict
) -> int:
    """Shared POST-and-render for every create/update/transition command
    whose path is nested under a Tenant -- i.e. everything but `tenant
    create` itself, see `_post_and_show_flat`. `suffix` is relative to the
    Tenant, e.g. `/capabilities`."""
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token, tenant_id=tenant_id)
    try:
        result = await client.post(client.tenant_path(suffix), payload)
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_detail(result)
    return 0


async def _post_and_show_flat(config: RootConfig, path: str, payload: dict) -> int:
    """Shared POST-and-render for `tenant create` -- the one route with no
    Tenant of its own to nest under (it's creating one), so `path` is
    already the full API path, not a Tenant-relative suffix."""
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token)
    try:
        result = await client.post(path, payload)
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_detail(result)
    return 0


async def _patch_and_show(
    config: RootConfig, tenant_id: uuid.UUID, suffix: str, payload: dict
) -> int:
    """Shared PATCH-and-render for Tenant-nested in-place updates (e.g.
    `environment update`) -- unlike `_patch_and_show_flat` (Tenant itself),
    `suffix` is relative to the Tenant, like `_post_and_show`. Drops `None`
    values from `payload` before sending: `EnvironmentUpdate`'s fields are
    all optional (partial update), and while the server already treats an
    explicit `null` the same as an absent key (see `EnvironmentService.
    update`), sending only what the caller actually set keeps the request
    honest about intent."""
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token, tenant_id=tenant_id)
    clean_payload = {k: v for k, v in payload.items() if v is not None}
    try:
        result = await client.patch(client.tenant_path(suffix), clean_payload)
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_detail(result)
    return 0


async def _patch_and_show_flat(config: RootConfig, path: str, payload: dict) -> int:
    """Shared PATCH-and-render for `tenant update` -- unlike Capability/
    ModelEndpoint/Agent's `update`, which POSTs a new version, Tenant
    mutates in place, and (like `tenant create`) has no Tenant of its own
    to nest under."""
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token)
    try:
        result = await client.patch(path, payload)
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_detail(result)
    return 0


# --- Resource registry ---------------------------------------------------

# Shared VersionedEntityRead columns (see model/schemas/base.py) are enough
# for a `list`/`versions` table across all three resources -- resource-
# specific fields (protocol, layer, ...) are still visible via `show`.
_LIST_COLUMNS = ('entity_id', 'name', 'version', 'lifecycle_state', 'maturity')


@dataclasses.dataclass(frozen=True)
class ResourceSpec:
    """Everything the generic list/show/versions/transition verbs below
    need to know about one Catalog resource. `api_path` is relative to the
    Tenant (`CatalogClient.tenant_path`), not a full path -- every
    resource but Tenant itself is nested under one."""

    title: str
    plural: str  # explicit, not `title + 's'` -- "Capability" pluralizes irregularly
    article: str  # 'a' or 'an', for the generated help text
    api_path: str
    list_columns: tuple[str, ...] = _LIST_COLUMNS


RESOURCES: dict[str, ResourceSpec] = {
    'capability': ResourceSpec(
        title='Capability', plural='Capabilities', article='a', api_path='/capabilities'
    ),
    'model': ResourceSpec(
        title='ModelEndpoint',
        plural='ModelEndpoints',
        article='a',
        api_path='/model-endpoints',
    ),
    'agent': ResourceSpec(
        title='Agent', plural='Agents', article='an', api_path='/agents'
    ),
    'skill': ResourceSpec(
        title='Skill', plural='Skills', article='a', api_path='/skills'
    ),
    'tool': ResourceSpec(title='Tool', plural='Tools', article='a', api_path='/tools'),
    'datasource': ResourceSpec(
        title='DataSource',
        plural='DataSources',
        article='a',
        api_path='/datasources',
    ),
    'dataproduct': ResourceSpec(
        title='DataProduct',
        plural='DataProducts',
        article='a',
        api_path='/dataproducts',
    ),
}


# --- Generic verbs (identical across resources) ---------------------------


async def resource_list(
    spec: ResourceSpec, config: RootConfig, args: argparse.Namespace
) -> int:
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token, tenant_id=tenant_id)
    params = {
        'lifecycle_state': args.lifecycle_state,
        'limit': args.limit,
        'offset': args.offset,
    }
    try:
        page = await client.get(client.tenant_path(spec.api_path), params=params)
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_table(spec.list_columns, page['items'])
    console.print(
        f'[dim]{len(page["items"])} of {page["total"]} shown '
        f'(limit={page["limit"]}, offset={page["offset"]})[/dim]'
    )
    return 0


async def resource_show(
    spec: ResourceSpec, config: RootConfig, args: argparse.Namespace
) -> int:
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token, tenant_id=tenant_id)
    path = client.tenant_path(f'{spec.api_path}/{args.entity_id}')
    if args.version is not None:
        path = f'{path}/versions/{args.version}'
    try:
        item = await client.get(path)
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_detail(item)
    return 0


async def resource_versions(
    spec: ResourceSpec, config: RootConfig, args: argparse.Namespace
) -> int:
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token, tenant_id=tenant_id)
    try:
        items = await client.get(
            client.tenant_path(f'{spec.api_path}/{args.entity_id}/versions')
        )
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_table(spec.list_columns, items)
    console.print(f'[dim]{len(items)} version(s)[/dim]')
    return 0


async def resource_transition(
    spec: ResourceSpec, config: RootConfig, args: argparse.Namespace
) -> int:
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    suffix = f'{spec.api_path}/{args.entity_id}/versions/{args.version}/transitions'
    return await _post_and_show(config, tenant_id, suffix, {'to_state': args.to_state})


async def resource_create(
    spec: ResourceSpec,
    payload_fn: typing.Callable[[argparse.Namespace], dict | None],
    config: RootConfig,
    args: argparse.Namespace,
) -> int:
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    payload = payload_fn(args)
    if payload is None:
        return 1
    return await _post_and_show(config, tenant_id, spec.api_path, payload)


async def resource_update(
    spec: ResourceSpec,
    payload_fn: typing.Callable[[argparse.Namespace], dict | None],
    config: RootConfig,
    args: argparse.Namespace,
) -> int:
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    payload = payload_fn(args)
    if payload is None:
        return 1
    suffix = f'{spec.api_path}/{args.entity_id}/versions'
    return await _post_and_show(config, tenant_id, suffix, payload)


# --- Capability: create/update ---------------------------------------------


def _capability_payload(args: argparse.Namespace) -> dict | None:
    try:
        target_metrics = _parse_json(args.target_metrics, '--target-metrics', list)
    except (ValueError, TypeError) as exc:
        console.print(f'[red]{exc}[/red]')
        return None
    return {
        'name': args.name,
        'description': args.description,
        'target_metrics': target_metrics,
    }


capability_create = functools.partial(
    resource_create, RESOURCES['capability'], _capability_payload
)
capability_update = functools.partial(
    resource_update, RESOURCES['capability'], _capability_payload
)


# --- ModelEndpoint: create/update -------------------------------------------


def _model_payload(args: argparse.Namespace) -> dict | None:
    return {
        'name': args.name,
        'description': args.description,
        'protocol': args.protocol,
        'base_url': args.base_url,
        'model': args.model,
        'auth_binding_id': _uuid_str(args.auth_binding_id),
    }


model_create = functools.partial(resource_create, RESOURCES['model'], _model_payload)
model_update = functools.partial(resource_update, RESOURCES['model'], _model_payload)


# --- Agent: create/update ----------------------------------------------------


def _agent_payload(args: argparse.Namespace) -> dict | None:
    try:
        llm_config = _parse_json(args.llm_config, '--llm-config', dict)
        permission_boundary = _parse_json(
            args.permission_boundary, '--permission-boundary', dict
        )
    except (ValueError, TypeError) as exc:
        console.print(f'[red]{exc}[/red]')
        return None
    try:
        prompt = pathlib.Path(args.prompt_file).read_text()
    except OSError as exc:
        console.print(f'[red]--prompt-file: {exc}[/red]')
        return None
    return {
        'name': args.name,
        'description': args.description,
        'layer': args.layer,
        'model_binding_id': _uuid_str(args.model_binding_id),
        'llm_config': llm_config,
        'prompt': prompt,
        'memory_scope': args.memory_scope,
        'permission_boundary': permission_boundary,
    }


agent_create = functools.partial(resource_create, RESOURCES['agent'], _agent_payload)
agent_update = functools.partial(resource_update, RESOURCES['agent'], _agent_payload)


# --- Skill: create/update, plus graph node/edge sub-resources ---------------


def _skill_payload(args: argparse.Namespace) -> dict | None:
    try:
        atomic_content = (
            _parse_json(args.atomic_content, '--atomic-content', dict)
            if args.atomic_content is not None
            else None
        )
    except (ValueError, TypeError) as exc:
        console.print(f'[red]{exc}[/red]')
        return None
    return {
        'name': args.name,
        'description': args.description,
        'layer': args.layer,
        'kind': args.kind,
        'is_entry_point': args.is_entry_point,
        'atomic_content': atomic_content,
    }


# A deployable workflow is just a Composite Skill with `--is-entry-point`
# -- there is no separate "Topology" resource (see CLAUDE.md's entity model).
skill_create = functools.partial(resource_create, RESOURCES['skill'], _skill_payload)
skill_update = functools.partial(resource_update, RESOURCES['skill'], _skill_payload)


_SKILL_NODE_COLUMNS = (
    'id',
    'node_key',
    'node_type',
    'agent_id',
    'skill_ref_id',
    'tool_id',
)
_SKILL_EDGE_COLUMNS = ('id', 'from_node_id', 'to_node_id')


async def skill_node_add(config: RootConfig, args: argparse.Namespace) -> int:
    """Add a node to one version of a Skill's graph via the Catalog API."""
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    try:
        position = (
            _parse_json(args.position, '--position', dict)
            if args.position is not None
            else None
        )
    except (ValueError, TypeError) as exc:
        console.print(f'[red]{exc}[/red]')
        return 1
    payload = {
        'node_key': args.node_key,
        'node_type': args.node_type,
        'agent_id': _uuid_str(args.agent_id),
        'skill_ref_id': _uuid_str(args.skill_ref_id),
        'tool_id': _uuid_str(args.tool_id),
        'position': position,
    }
    suffix = (
        f'{RESOURCES["skill"].api_path}/{args.entity_id}/versions/{args.version}/nodes'
    )
    return await _post_and_show(config, tenant_id, suffix, payload)


async def skill_node_list(config: RootConfig, args: argparse.Namespace) -> int:
    """List the nodes of one version of a Skill's graph via the Catalog API."""
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token, tenant_id=tenant_id)
    suffix = (
        f'{RESOURCES["skill"].api_path}/{args.entity_id}/versions/{args.version}/nodes'
    )
    try:
        items = await client.get(client.tenant_path(suffix))
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_table(_SKILL_NODE_COLUMNS, items)
    console.print(f'[dim]{len(items)} node(s)[/dim]')
    return 0


async def skill_edge_add(config: RootConfig, args: argparse.Namespace) -> int:
    """Add an edge to one version of a Skill's graph via the Catalog API."""
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    payload = {
        'from_node_id': str(args.from_node_id),
        'to_node_id': str(args.to_node_id),
    }
    suffix = (
        f'{RESOURCES["skill"].api_path}/{args.entity_id}/versions/{args.version}/edges'
    )
    return await _post_and_show(config, tenant_id, suffix, payload)


async def skill_edge_list(config: RootConfig, args: argparse.Namespace) -> int:
    """List the edges of one version of a Skill's graph via the Catalog API."""
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token, tenant_id=tenant_id)
    suffix = (
        f'{RESOURCES["skill"].api_path}/{args.entity_id}/versions/{args.version}/edges'
    )
    try:
        items = await client.get(client.tenant_path(suffix))
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_table(_SKILL_EDGE_COLUMNS, items)
    console.print(f'[dim]{len(items)} edge(s)[/dim]')
    return 0


# --- Tool: create/update, plus data-binding sub-resource ---------------------


def _tool_payload(args: argparse.Namespace) -> dict | None:
    try:
        invocation_spec = _parse_json(args.invocation_spec, '--invocation-spec', dict)
    except (ValueError, TypeError) as exc:
        console.print(f'[red]{exc}[/red]')
        return None
    return {
        'name': args.name,
        'description': args.description,
        'invocation_spec': invocation_spec,
        'auth_binding_id': _uuid_str(args.auth_binding_id),
    }


# Tools bind to DataSource/DataProduct statically at design time (`tool
# data-binding add`), unlike an Agent, which never gets a direct data
# binding (see CLAUDE.md).
tool_create = functools.partial(resource_create, RESOURCES['tool'], _tool_payload)
tool_update = functools.partial(resource_update, RESOURCES['tool'], _tool_payload)


_TOOL_BINDING_COLUMNS = ('id', 'datasource_id', 'dataproduct_id', 'access_mode')


async def tool_binding_add(config: RootConfig, args: argparse.Namespace) -> int:
    """Add a data binding to one version of a Tool via the Catalog API."""
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    payload = {
        'datasource_id': _uuid_str(args.datasource_id),
        'dataproduct_id': _uuid_str(args.dataproduct_id),
        'access_mode': args.access_mode,
    }
    suffix = (
        f'{RESOURCES["tool"].api_path}/{args.entity_id}/versions/{args.version}'
        '/data-bindings'
    )
    return await _post_and_show(config, tenant_id, suffix, payload)


async def tool_binding_list(config: RootConfig, args: argparse.Namespace) -> int:
    """List the data bindings of one version of a Tool via the Catalog API."""
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token, tenant_id=tenant_id)
    suffix = (
        f'{RESOURCES["tool"].api_path}/{args.entity_id}/versions/{args.version}'
        '/data-bindings'
    )
    try:
        items = await client.get(client.tenant_path(suffix))
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_table(_TOOL_BINDING_COLUMNS, items)
    console.print(f'[dim]{len(items)} data binding(s)[/dim]')
    return 0


# --- DataSource: create/update ------------------------------------------


def _datasource_payload(args: argparse.Namespace) -> dict:
    return {
        'name': args.name,
        'description': args.description,
        'kind': args.kind,
        'connection_binding_id': _uuid_str(args.connection_binding_id),
    }


datasource_create = functools.partial(
    resource_create, RESOURCES['datasource'], _datasource_payload
)
datasource_update = functools.partial(
    resource_update, RESOURCES['datasource'], _datasource_payload
)


# --- DataProduct: create/update, plus lineage sub-resource --------------


def _dataproduct_payload(args: argparse.Namespace) -> dict | None:
    try:
        contract = _parse_json(args.contract, '--contract', dict)
    except (ValueError, TypeError) as exc:
        console.print(f'[red]{exc}[/red]')
        return None
    return {
        'name': args.name,
        'description': args.description,
        'contract': contract,
    }


dataproduct_create = functools.partial(
    resource_create, RESOURCES['dataproduct'], _dataproduct_payload
)
dataproduct_update = functools.partial(
    resource_update, RESOURCES['dataproduct'], _dataproduct_payload
)


_DATAPRODUCT_LINEAGE_COLUMNS = (
    'id',
    'dataproduct_id',
    'source_datasource_id',
    'source_dataproduct_id',
)


async def dataproduct_lineage_add(config: RootConfig, args: argparse.Namespace) -> int:
    """Add a lineage edge to one version of a DataProduct via the Catalog
    API."""
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    payload = {
        'source_datasource_id': _uuid_str(args.source_datasource_id),
        'source_dataproduct_id': _uuid_str(args.source_dataproduct_id),
    }
    suffix = (
        f'{RESOURCES["dataproduct"].api_path}/{args.entity_id}/versions'
        f'/{args.version}/lineage'
    )
    return await _post_and_show(config, tenant_id, suffix, payload)


async def dataproduct_lineage_list(config: RootConfig, args: argparse.Namespace) -> int:
    """List the lineage edges of one version of a DataProduct via the
    Catalog API."""
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token, tenant_id=tenant_id)
    suffix = (
        f'{RESOURCES["dataproduct"].api_path}/{args.entity_id}/versions'
        f'/{args.version}/lineage'
    )
    try:
        items = await client.get(client.tenant_path(suffix))
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_table(_DATAPRODUCT_LINEAGE_COLUMNS, items)
    console.print(f'[dim]{len(items)} lineage edge(s)[/dim]')
    return 0


# --- Environment: CRUD (not a VersionedEntity) ---------------------------
#
# No lifecycle_state/version/transitions -- Environment is the platform-tier
# "physically isolated execution context" (Sandbox/Staging/Production),
# not something that goes through Draft->...->Retired. `update` PATCHes in
# place, like Tenant, but (unlike Tenant) it *is* nested under a Tenant
# path.

_ENVIRONMENT_API_PATH = '/environments'
_ENVIRONMENT_LIST_COLUMNS = ('id', 'name', 'kind', 'compute_boundary_ref')


async def environment_create(config: RootConfig, args: argparse.Namespace) -> int:
    """Create an Environment via the Catalog API."""
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    payload = {
        'name': args.name,
        'kind': args.kind,
        'compute_boundary_ref': args.compute_boundary_ref,
        'network_boundary_ref': args.network_boundary_ref,
    }
    return await _post_and_show(config, tenant_id, _ENVIRONMENT_API_PATH, payload)


async def environment_list(config: RootConfig, args: argparse.Namespace) -> int:
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token, tenant_id=tenant_id)
    params = {'limit': args.limit, 'offset': args.offset}
    try:
        page = await client.get(
            client.tenant_path(_ENVIRONMENT_API_PATH), params=params
        )
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_table(_ENVIRONMENT_LIST_COLUMNS, page['items'])
    console.print(
        f'[dim]{len(page["items"])} of {page["total"]} shown '
        f'(limit={page["limit"]}, offset={page["offset"]})[/dim]'
    )
    return 0


async def environment_show(config: RootConfig, args: argparse.Namespace) -> int:
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token, tenant_id=tenant_id)
    try:
        item = await client.get(
            client.tenant_path(f'{_ENVIRONMENT_API_PATH}/{args.environment_id}')
        )
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_detail(item)
    return 0


async def environment_update(config: RootConfig, args: argparse.Namespace) -> int:
    """Update an Environment's boundary refs in place via the Catalog API."""
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    payload = {
        'compute_boundary_ref': args.compute_boundary_ref,
        'network_boundary_ref': args.network_boundary_ref,
    }
    suffix = f'{_ENVIRONMENT_API_PATH}/{args.environment_id}'
    return await _patch_and_show(config, tenant_id, suffix, payload)


# --- Tenant/Principal: CRUD ---------------------------------------------
#
# Not VersionedEntity -- no lifecycle_state/version/transitions, so these
# don't go through RESOURCES/_add_generic_verbs (built for the versioned
# trio above). Tenant's `update` is a PATCH in place, not a new version.
# Principal has no `update` at all -- its only mutable field used to be
# display_name, which no longer exists (a Principal's human-readable name
# lives in the IDP's `name` claim, not stored here; see
# `src/loom/model/tenant.py`'s `Principal` docstring). Neither resource
# has a delete endpoint (see the routers).
#
# Bootstrapping the very first Tenant/Principal in a fresh deployment does
# go through these: POST /tenants and POST /principals only require the
# caller's token to carry `catalog:tenant:write`/`catalog:principal:write`
# (granted by the `catalog-platform-admin` role -- see `loom idp register`
# and docs/admin-guide.md's "Platform administrator" section), not an
# already-provisioned Principal. The platform administrator's own account
# never needs a Principal row for this; one is only needed once they (or
# anyone else) want to act as a Principal within a specific Tenant.

_TENANT_API_PATH = '/api/v1/tenants'
_TENANT_LIST_COLUMNS = ('id', 'slug', 'name')
_PRINCIPAL_API_PATH = '/principals'
_PRINCIPAL_LIST_COLUMNS = ('id', 'tenant_id', 'kind', 'external_id')


async def tenant_create(config: RootConfig, args: argparse.Namespace) -> int:
    """Create a Tenant via the Catalog API. Not nested under a Tenant path
    -- it's the one resource this doesn't apply to (see
    `_post_and_show_flat`)."""
    payload = {'slug': args.slug, 'name': args.name}
    return await _post_and_show_flat(config, _TENANT_API_PATH, payload)


async def tenant_list(config: RootConfig, args: argparse.Namespace) -> int:
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token)
    params = {'limit': args.limit, 'offset': args.offset}
    try:
        page = await client.get(_TENANT_API_PATH, params=params)
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_table(_TENANT_LIST_COLUMNS, page['items'])
    console.print(
        f'[dim]{len(page["items"])} of {page["total"]} shown '
        f'(limit={page["limit"]}, offset={page["offset"]})[/dim]'
    )
    return 0


async def tenant_show(config: RootConfig, args: argparse.Namespace) -> int:
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token)
    try:
        item = await client.get(f'{_TENANT_API_PATH}/{args.tenant_id}')
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_detail(item)
    return 0


async def tenant_update(config: RootConfig, args: argparse.Namespace) -> int:
    """Update a Tenant's name via the Catalog API."""
    path = f'{_TENANT_API_PATH}/{args.tenant_id}'
    return await _patch_and_show_flat(config, path, {'name': args.name})


async def principal_create(config: RootConfig, args: argparse.Namespace) -> int:
    """Create a Principal via the Catalog API, in the Tenant named by
    --tenant-id (always required, no session default -- creating a
    Principal is a durable write, a worse case for silently defaulting to
    an ambient selection than reading is; see `loom tenant create`/`loom
    tenant list` for the Tenant's id)."""
    payload = {'kind': args.kind, 'external_id': args.external_id}
    return await _post_and_show(config, args.tenant_id, _PRINCIPAL_API_PATH, payload)


async def principal_list(config: RootConfig, args: argparse.Namespace) -> int:
    """List Principals in a Tenant via the Catalog API. `--tenant-id`
    defaults to the locally-selected Tenant (`loom auth set-tenant`,
    normally set automatically by `loom auth login`) -- explicit
    `--tenant-id` still overrides it, e.g. a platform admin listing a
    Tenant other than their own selection."""
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token, tenant_id=tenant_id)
    params = {'limit': args.limit, 'offset': args.offset}
    try:
        page = await client.get(client.tenant_path(_PRINCIPAL_API_PATH), params=params)
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_table(_PRINCIPAL_LIST_COLUMNS, page['items'])
    console.print(
        f'[dim]{len(page["items"])} of {page["total"]} shown '
        f'(limit={page["limit"]}, offset={page["offset"]})[/dim]'
    )
    return 0


async def principal_show(config: RootConfig, args: argparse.Namespace) -> int:
    tenant_id = _resolve_tenant_id(config, args)
    if tenant_id is None:
        return 1
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token, tenant_id=tenant_id)
    try:
        item = await client.get(
            client.tenant_path(f'{_PRINCIPAL_API_PATH}/{args.principal_id}')
        )
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_detail(item)
    return 0


# --- argparse wiring ---------------------------------------------------


def _add_tenant_id_arg(parser: argparse.ArgumentParser) -> None:
    """Every resource but Tenant itself is nested under one -- this is the
    override; the default is `config.auth.session.tenant_id` (see
    `_resolve_tenant_id`)."""
    parser.add_argument(
        '--tenant-id',
        dest='tenant_id',
        type=uuid.UUID,
        default=None,
        help=(
            'The Tenant to operate in, defaults to the locally-selected '
            'Tenant (see `loom auth set-tenant`)'
        ),
    )


def _add_capability_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('name', help='Human-readable name')
    parser.add_argument('--description', default=None, help='Free-text description')
    parser.add_argument(
        '--target-metrics',
        dest='target_metrics',
        default='[]',
        help='Target metrics as a JSON array of objects, defaults to []',
    )
    _add_tenant_id_arg(parser)


def _add_model_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('name', help='Human-readable name')
    parser.add_argument('--description', default=None, help='Free-text description')
    parser.add_argument(
        '--protocol',
        required=True,
        choices=[p.value for p in ModelProtocol],
        help='Wire protocol the endpoint speaks',
    )
    parser.add_argument(
        '--base-url',
        dest='base_url',
        default=None,
        help='Endpoint base URL, required when --protocol=openai_compatible',
    )
    parser.add_argument(
        '--model',
        dest='model',
        required=True,
        help='The model identifier at the endpoint, e.g. gpt-4o',
    )
    parser.add_argument(
        '--auth-binding-id',
        dest='auth_binding_id',
        type=uuid.UUID,
        default=None,
        help='Credential vault binding for this endpoint',
    )
    _add_tenant_id_arg(parser)


def _add_agent_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('name', help='Human-readable name')
    parser.add_argument('--description', default=None, help='Free-text description')
    parser.add_argument(
        '--layer',
        required=True,
        choices=[layer.value for layer in Layer],
        help='Skill Graph layer this Agent belongs to',
    )
    parser.add_argument(
        '--model-binding-id',
        dest='model_binding_id',
        type=uuid.UUID,
        default=None,
        help="The bound ModelEndpoint's entity_id, e.g. from `loom model create`",
    )
    parser.add_argument(
        '--llm-config',
        dest='llm_config',
        default='{}',
        help='Model invocation overrides as a JSON object, defaults to {}',
    )
    parser.add_argument(
        '--prompt-file',
        dest='prompt_file',
        required=True,
        help='Path to a file containing the versioned system prompt',
    )
    parser.add_argument(
        '--memory-scope',
        dest='memory_scope',
        required=True,
        choices=[scope.value for scope in MemoryScope],
        help='Agent memory scope',
    )
    parser.add_argument(
        '--permission-boundary',
        dest='permission_boundary',
        default='{}',
        help='Permission boundary as a JSON object, defaults to {}',
    )
    _add_tenant_id_arg(parser)


def _add_skill_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('name', help='Human-readable name')
    parser.add_argument('--description', default=None, help='Free-text description')
    parser.add_argument(
        '--layer',
        required=True,
        choices=[layer.value for layer in Layer],
        help='Skill Graph layer this Skill belongs to',
    )
    parser.add_argument(
        '--kind',
        required=True,
        choices=[k.value for k in SkillKind],
        help='Atomic (prompt/code) or composite (a graph of nodes)',
    )
    parser.add_argument(
        '--is-entry-point',
        dest='is_entry_point',
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            'A deployable workflow is a Composite Skill with this set. '
            "`update` restates the entire payload (see `loom skill --help`'s "
            'note on versioning) -- pass `--is-entry-point`/'
            '`--no-is-entry-point` explicitly rather than relying on the '
            'default when updating a Skill that already has this set.'
        ),
    )
    parser.add_argument(
        '--atomic-content',
        dest='atomic_content',
        default=None,
        help='Atomic Skill content as a JSON object, required when --kind=atomic',
    )
    _add_tenant_id_arg(parser)


def _add_tool_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('name', help='Human-readable name')
    parser.add_argument('--description', default=None, help='Free-text description')
    parser.add_argument(
        '--invocation-spec',
        dest='invocation_spec',
        required=True,
        help='The external action interface as a JSON object',
    )
    parser.add_argument(
        '--auth-binding-id',
        dest='auth_binding_id',
        type=uuid.UUID,
        default=None,
        help='Credential vault binding for this Tool',
    )
    _add_tenant_id_arg(parser)


def _add_datasource_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('name', help='Human-readable name')
    parser.add_argument('--description', default=None, help='Free-text description')
    parser.add_argument(
        '--kind',
        required=True,
        choices=[k.value for k in DataSourceKind],
        help='What this DataSource connects to',
    )
    parser.add_argument(
        '--connection-binding-id',
        dest='connection_binding_id',
        type=uuid.UUID,
        default=None,
        help='Credential vault binding for this connection',
    )
    _add_tenant_id_arg(parser)


def _add_dataproduct_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('name', help='Human-readable name')
    parser.add_argument('--description', default=None, help='Free-text description')
    parser.add_argument(
        '--contract', required=True, help='The publication contract as a JSON object'
    )
    _add_tenant_id_arg(parser)


def _add_generic_verbs(sub: argparse._SubParsersAction, spec: ResourceSpec) -> None:
    """The four verbs identical in shape across every resource: list, show,
    versions, transition. create/update are wired separately, per resource
    (see `_add_*_fields` above and `resource_create`/`resource_update`
    below) -- their argparse fields differ per resource even though their
    execution (resolve tenant, build payload, POST) is generic."""
    list_parser = sub.add_parser('list', help=f'List {spec.plural}')
    list_parser.add_argument(
        '--lifecycle-state',
        dest='lifecycle_state',
        default=None,
        choices=[s.value for s in LifecycleState],
        help='Filter by lifecycle state',
    )
    list_parser.add_argument(
        '--limit', type=int, default=50, help='Max results, defaults to 50'
    )
    list_parser.add_argument(
        '--offset', type=int, default=0, help='Pagination offset, defaults to 0'
    )
    _add_tenant_id_arg(list_parser)
    list_parser.set_defaults(func=functools.partial(resource_list, spec))

    show_parser = sub.add_parser('show', help=f'Show {spec.article} {spec.title}')
    show_parser.add_argument('entity_id', type=uuid.UUID)
    show_parser.add_argument(
        '--version',
        type=int,
        default=None,
        help='Show a specific version instead of the current one',
    )
    _add_tenant_id_arg(show_parser)
    show_parser.set_defaults(func=functools.partial(resource_show, spec))

    versions_parser = sub.add_parser(
        'versions', help=f'List every version of {spec.article} {spec.title}'
    )
    versions_parser.add_argument('entity_id', type=uuid.UUID)
    _add_tenant_id_arg(versions_parser)
    versions_parser.set_defaults(func=functools.partial(resource_versions, spec))

    transition_parser = sub.add_parser(
        'transition',
        help=f'Transition {spec.article} {spec.title} to a new lifecycle state',
    )
    transition_parser.add_argument('entity_id', type=uuid.UUID)
    transition_parser.add_argument('version', type=int)
    transition_parser.add_argument(
        'to_state', choices=[s.value for s in LifecycleState]
    )
    _add_tenant_id_arg(transition_parser)
    transition_parser.set_defaults(func=functools.partial(resource_transition, spec))


def _add_capability_parsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser('capability', help='Capability commands')
    sub = parser.add_subparsers(required=True)

    create_parser = sub.add_parser('create', help='Create a Capability')
    _add_capability_fields(create_parser)
    create_parser.set_defaults(func=capability_create)

    update_parser = sub.add_parser('update', help='Create a new Capability version')
    update_parser.add_argument(
        'entity_id', type=uuid.UUID, help="The Capability's entity_id"
    )
    _add_capability_fields(update_parser)
    update_parser.set_defaults(func=capability_update)

    _add_generic_verbs(sub, RESOURCES['capability'])


def _add_model_parsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser('model', help='ModelEndpoint commands')
    sub = parser.add_subparsers(required=True)

    create_parser = sub.add_parser('create', help='Create a ModelEndpoint')
    _add_model_fields(create_parser)
    create_parser.set_defaults(func=model_create)

    update_parser = sub.add_parser('update', help='Create a new ModelEndpoint version')
    update_parser.add_argument(
        'entity_id', type=uuid.UUID, help="The ModelEndpoint's entity_id"
    )
    _add_model_fields(update_parser)
    update_parser.set_defaults(func=model_update)

    _add_generic_verbs(sub, RESOURCES['model'])


def _add_agent_parsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser('agent', help='Agent commands')
    sub = parser.add_subparsers(required=True)

    create_parser = sub.add_parser('create', help='Create an Agent')
    _add_agent_fields(create_parser)
    create_parser.set_defaults(func=agent_create)

    update_parser = sub.add_parser('update', help='Create a new Agent version')
    update_parser.add_argument(
        'entity_id', type=uuid.UUID, help="The Agent's entity_id"
    )
    _add_agent_fields(update_parser)
    update_parser.set_defaults(func=agent_update)

    _add_generic_verbs(sub, RESOURCES['agent'])


def _add_skill_parsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser('skill', help='Skill commands')
    sub = parser.add_subparsers(required=True)

    create_parser = sub.add_parser('create', help='Create a Skill')
    _add_skill_fields(create_parser)
    create_parser.set_defaults(func=skill_create)

    update_parser = sub.add_parser('update', help='Create a new Skill version')
    update_parser.add_argument(
        'entity_id', type=uuid.UUID, help="The Skill's entity_id"
    )
    _add_skill_fields(update_parser)
    update_parser.set_defaults(func=skill_update)

    _add_generic_verbs(sub, RESOURCES['skill'])

    node_parser = sub.add_parser('node', help="A Skill version's graph nodes")
    node_sub = node_parser.add_subparsers(required=True)

    node_add_parser = node_sub.add_parser('add', help='Add a node')
    node_add_parser.add_argument('entity_id', type=uuid.UUID)
    node_add_parser.add_argument('version', type=int)
    node_add_parser.add_argument(
        'node_key', help='Unique key of this node in the graph'
    )
    node_add_parser.add_argument(
        '--node-type',
        dest='node_type',
        required=True,
        choices=[t.value for t in GraphNodeType],
    )
    node_add_parser.add_argument(
        '--agent-id', dest='agent_id', type=uuid.UUID, default=None
    )
    node_add_parser.add_argument(
        '--skill-ref-id', dest='skill_ref_id', type=uuid.UUID, default=None
    )
    node_add_parser.add_argument(
        '--tool-id', dest='tool_id', type=uuid.UUID, default=None
    )
    node_add_parser.add_argument(
        '--position', default=None, help='Canvas position as a JSON object'
    )
    _add_tenant_id_arg(node_add_parser)
    node_add_parser.set_defaults(func=skill_node_add)

    node_list_parser = node_sub.add_parser('list', help='List nodes')
    node_list_parser.add_argument('entity_id', type=uuid.UUID)
    node_list_parser.add_argument('version', type=int)
    _add_tenant_id_arg(node_list_parser)
    node_list_parser.set_defaults(func=skill_node_list)

    edge_parser = sub.add_parser('edge', help="A Skill version's graph edges")
    edge_sub = edge_parser.add_subparsers(required=True)

    edge_add_parser = edge_sub.add_parser('add', help='Add an edge')
    edge_add_parser.add_argument('entity_id', type=uuid.UUID)
    edge_add_parser.add_argument('version', type=int)
    edge_add_parser.add_argument('from_node_id', type=uuid.UUID)
    edge_add_parser.add_argument('to_node_id', type=uuid.UUID)
    _add_tenant_id_arg(edge_add_parser)
    edge_add_parser.set_defaults(func=skill_edge_add)

    edge_list_parser = edge_sub.add_parser('list', help='List edges')
    edge_list_parser.add_argument('entity_id', type=uuid.UUID)
    edge_list_parser.add_argument('version', type=int)
    _add_tenant_id_arg(edge_list_parser)
    edge_list_parser.set_defaults(func=skill_edge_list)


def _add_tool_parsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser('tool', help='Tool commands')
    sub = parser.add_subparsers(required=True)

    create_parser = sub.add_parser('create', help='Create a Tool')
    _add_tool_fields(create_parser)
    create_parser.set_defaults(func=tool_create)

    update_parser = sub.add_parser('update', help='Create a new Tool version')
    update_parser.add_argument('entity_id', type=uuid.UUID, help="The Tool's entity_id")
    _add_tool_fields(update_parser)
    update_parser.set_defaults(func=tool_update)

    _add_generic_verbs(sub, RESOURCES['tool'])

    binding_parser = sub.add_parser('binding', help="A Tool version's data bindings")
    binding_sub = binding_parser.add_subparsers(required=True)

    binding_add_parser = binding_sub.add_parser('add', help='Add a data binding')
    binding_add_parser.add_argument('entity_id', type=uuid.UUID)
    binding_add_parser.add_argument('version', type=int)
    binding_add_parser.add_argument(
        '--datasource-id', dest='datasource_id', type=uuid.UUID, default=None
    )
    binding_add_parser.add_argument(
        '--dataproduct-id', dest='dataproduct_id', type=uuid.UUID, default=None
    )
    binding_add_parser.add_argument(
        '--access-mode',
        dest='access_mode',
        required=True,
        choices=[m.value for m in DataBindingAccessMode],
    )
    _add_tenant_id_arg(binding_add_parser)
    binding_add_parser.set_defaults(func=tool_binding_add)

    binding_list_parser = binding_sub.add_parser('list', help='List data bindings')
    binding_list_parser.add_argument('entity_id', type=uuid.UUID)
    binding_list_parser.add_argument('version', type=int)
    _add_tenant_id_arg(binding_list_parser)
    binding_list_parser.set_defaults(func=tool_binding_list)


def _add_datasource_parsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser('datasource', help='DataSource commands')
    sub = parser.add_subparsers(required=True)

    create_parser = sub.add_parser('create', help='Create a DataSource')
    _add_datasource_fields(create_parser)
    create_parser.set_defaults(func=datasource_create)

    update_parser = sub.add_parser('update', help='Create a new DataSource version')
    update_parser.add_argument(
        'entity_id', type=uuid.UUID, help="The DataSource's entity_id"
    )
    _add_datasource_fields(update_parser)
    update_parser.set_defaults(func=datasource_update)

    _add_generic_verbs(sub, RESOURCES['datasource'])


def _add_dataproduct_parsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser('dataproduct', help='DataProduct commands')
    sub = parser.add_subparsers(required=True)

    create_parser = sub.add_parser('create', help='Create a DataProduct')
    _add_dataproduct_fields(create_parser)
    create_parser.set_defaults(func=dataproduct_create)

    update_parser = sub.add_parser('update', help='Create a new DataProduct version')
    update_parser.add_argument(
        'entity_id', type=uuid.UUID, help="The DataProduct's entity_id"
    )
    _add_dataproduct_fields(update_parser)
    update_parser.set_defaults(func=dataproduct_update)

    _add_generic_verbs(sub, RESOURCES['dataproduct'])

    lineage_parser = sub.add_parser(
        'lineage', help="A DataProduct version's lineage edges"
    )
    lineage_sub = lineage_parser.add_subparsers(required=True)

    lineage_add_parser = lineage_sub.add_parser('add', help='Add a lineage edge')
    lineage_add_parser.add_argument('entity_id', type=uuid.UUID)
    lineage_add_parser.add_argument('version', type=int)
    lineage_add_parser.add_argument(
        '--source-datasource-id',
        dest='source_datasource_id',
        type=uuid.UUID,
        default=None,
    )
    lineage_add_parser.add_argument(
        '--source-dataproduct-id',
        dest='source_dataproduct_id',
        type=uuid.UUID,
        default=None,
    )
    _add_tenant_id_arg(lineage_add_parser)
    lineage_add_parser.set_defaults(func=dataproduct_lineage_add)

    lineage_list_parser = lineage_sub.add_parser('list', help='List lineage edges')
    lineage_list_parser.add_argument('entity_id', type=uuid.UUID)
    lineage_list_parser.add_argument('version', type=int)
    _add_tenant_id_arg(lineage_list_parser)
    lineage_list_parser.set_defaults(func=dataproduct_lineage_list)


def _add_environment_parsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser('environment', help='Environment commands')
    sub = parser.add_subparsers(required=True)

    create_parser = sub.add_parser('create', help='Create an Environment')
    create_parser.add_argument('name', help='Human-readable name')
    create_parser.add_argument(
        '--kind', required=True, choices=[k.value for k in EnvironmentKind]
    )
    create_parser.add_argument(
        '--compute-boundary-ref',
        dest='compute_boundary_ref',
        required=True,
        help='Reference to the isolated compute boundary (e.g. a cluster/namespace id)',
    )
    create_parser.add_argument(
        '--network-boundary-ref',
        dest='network_boundary_ref',
        required=True,
        help='Reference to the isolated network boundary (e.g. a VPC/subnet id)',
    )
    _add_tenant_id_arg(create_parser)
    create_parser.set_defaults(func=environment_create)

    list_parser = sub.add_parser('list', help='List Environments')
    list_parser.add_argument(
        '--limit', type=int, default=50, help='Max results, defaults to 50'
    )
    list_parser.add_argument(
        '--offset', type=int, default=0, help='Pagination offset, defaults to 0'
    )
    _add_tenant_id_arg(list_parser)
    list_parser.set_defaults(func=environment_list)

    show_parser = sub.add_parser('show', help='Show an Environment')
    show_parser.add_argument('environment_id', type=uuid.UUID)
    _add_tenant_id_arg(show_parser)
    show_parser.set_defaults(func=environment_show)

    update_parser = sub.add_parser(
        'update', help="Update an Environment's boundary refs in place"
    )
    update_parser.add_argument('environment_id', type=uuid.UUID)
    update_parser.add_argument(
        '--compute-boundary-ref', dest='compute_boundary_ref', default=None
    )
    update_parser.add_argument(
        '--network-boundary-ref', dest='network_boundary_ref', default=None
    )
    _add_tenant_id_arg(update_parser)
    update_parser.set_defaults(func=environment_update)


def _add_tenant_parsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser('tenant', help='Tenant commands')
    sub = parser.add_subparsers(required=True)

    create_parser = sub.add_parser('create', help='Create a Tenant')
    create_parser.add_argument('slug', help='URL-safe unique slug')
    create_parser.add_argument('name', help='Human-readable name')
    create_parser.set_defaults(func=tenant_create)

    list_parser = sub.add_parser('list', help='List Tenants')
    list_parser.add_argument(
        '--limit', type=int, default=50, help='Max results, defaults to 50'
    )
    list_parser.add_argument(
        '--offset', type=int, default=0, help='Pagination offset, defaults to 0'
    )
    list_parser.set_defaults(func=tenant_list)

    show_parser = sub.add_parser('show', help='Show a Tenant')
    show_parser.add_argument('tenant_id', type=uuid.UUID)
    show_parser.set_defaults(func=tenant_show)

    update_parser = sub.add_parser('update', help="Update a Tenant's name")
    update_parser.add_argument('tenant_id', type=uuid.UUID)
    update_parser.add_argument('name', help='New name')
    update_parser.set_defaults(func=tenant_update)


def _add_principal_parsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser('principal', help='Principal commands')
    sub = parser.add_subparsers(required=True)

    create_parser = sub.add_parser('create', help='Create a Principal')
    create_parser.add_argument(
        '--tenant-id',
        dest='tenant_id',
        type=uuid.UUID,
        required=True,
        help=(
            'The Tenant to provision this Principal in -- no session '
            "claim to default from, since a caller's Tenant is resolved "
            'from their own Principal row, not a token claim'
        ),
    )
    create_parser.add_argument(
        '--kind',
        required=True,
        choices=[k.value for k in PrincipalKind],
        help='What this Principal represents',
    )
    create_parser.add_argument(
        '--external-id',
        dest='external_id',
        required=True,
        help="The IDP token's `sub` claim this Principal resolves to",
    )
    create_parser.set_defaults(func=principal_create)

    list_parser = sub.add_parser('list', help='List Principals in a Tenant')
    list_parser.add_argument(
        '--tenant-id',
        dest='tenant_id',
        type=uuid.UUID,
        required=False,
        default=None,
        help=(
            'The Tenant to list Principals in, defaults to the '
            'locally-selected Tenant (see `loom auth set-tenant`)'
        ),
    )
    list_parser.add_argument(
        '--limit', type=int, default=50, help='Max results, defaults to 50'
    )
    list_parser.add_argument(
        '--offset', type=int, default=0, help='Pagination offset, defaults to 0'
    )
    list_parser.set_defaults(func=principal_list)

    show_parser = sub.add_parser('show', help='Show a Principal')
    show_parser.add_argument('principal_id', type=uuid.UUID)
    _add_tenant_id_arg(show_parser)
    show_parser.set_defaults(func=principal_show)


def add_catalog_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register `loom capability|model|agent|skill|tool|datasource|
    dataproduct {create,update,list,show,versions,transition}` (plus each
    resource's own sub-resource verbs -- `skill node|edge`, `tool
    binding`, `dataproduct lineage`), `loom environment
    {create,list,show,update}`, `loom tenant {create,list,show,update}`,
    and `loom principal {create,list,show}` (no `update` -- see
    `_add_principal_parsers`) -- called once from `cli/main.py` so
    resource knowledge (payload shape, list columns, API path) stays here
    rather than growing `main.py`'s own argparse setup."""
    _add_capability_parsers(subparsers)
    _add_model_parsers(subparsers)
    _add_agent_parsers(subparsers)
    _add_skill_parsers(subparsers)
    _add_tool_parsers(subparsers)
    _add_datasource_parsers(subparsers)
    _add_dataproduct_parsers(subparsers)
    _add_environment_parsers(subparsers)
    _add_tenant_parsers(subparsers)
    _add_principal_parsers(subparsers)
