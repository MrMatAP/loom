import argparse
import dataclasses
import functools
import json
import time
import typing
import uuid

import rich.box
import rich.console
import rich.table

from loom.catalog_client import CatalogApiError, CatalogClient
from loom.config import RootConfig
from loom.model.enums import Layer, LifecycleState, MemoryScope, ModelProtocol

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
        console.print('[red]Not authorized.[/red] Run `loom auth login` again.')
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


async def _post_and_show(config: RootConfig, path: str, payload: dict) -> int:
    """Shared POST-and-render for every create/update/transition command."""
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


# --- Resource registry ---------------------------------------------------

# Shared VersionedEntityRead columns (see model/schemas/base.py) are enough
# for a `list`/`versions` table across all three resources -- resource-
# specific fields (protocol, layer, ...) are still visible via `show`.
_LIST_COLUMNS = ('entity_id', 'slug', 'name', 'version', 'lifecycle_state', 'maturity')


@dataclasses.dataclass(frozen=True)
class ResourceSpec:
    """Everything the generic list/show/versions/transition verbs below
    need to know about one Catalog resource."""

    title: str
    plural: str  # explicit, not `title + 's'` -- "Capability" pluralizes irregularly
    article: str  # 'a' or 'an', for the generated help text
    api_path: str
    list_columns: tuple[str, ...] = _LIST_COLUMNS


RESOURCES: dict[str, ResourceSpec] = {
    'capability': ResourceSpec(
        title='Capability',
        plural='Capabilities',
        article='a',
        api_path='/api/v1/capabilities',
    ),
    'model': ResourceSpec(
        title='ModelEndpoint',
        plural='ModelEndpoints',
        article='a',
        api_path='/api/v1/model-endpoints',
    ),
    'agent': ResourceSpec(
        title='Agent', plural='Agents', article='an', api_path='/api/v1/agents'
    ),
}


# --- Generic verbs (identical across resources) ---------------------------


async def resource_list(
    spec: ResourceSpec, config: RootConfig, args: argparse.Namespace
) -> int:
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token)
    params = {
        'lifecycle_state': args.lifecycle_state,
        'slug': args.slug,
        'limit': args.limit,
        'offset': args.offset,
    }
    try:
        page = await client.get(spec.api_path, params=params)
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
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token)
    path = f'{spec.api_path}/{args.entity_id}'
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
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token)
    try:
        items = await client.get(f'{spec.api_path}/{args.entity_id}/versions')
    except CatalogApiError as exc:
        return _print_api_error(exc)
    _print_table(spec.list_columns, items)
    console.print(f'[dim]{len(items)} version(s)[/dim]')
    return 0


async def resource_transition(
    spec: ResourceSpec, config: RootConfig, args: argparse.Namespace
) -> int:
    path = f'{spec.api_path}/{args.entity_id}/versions/{args.version}/transitions'
    return await _post_and_show(config, path, {'to_state': args.to_state})


# --- Capability: create/update ---------------------------------------------


def _capability_payload(args: argparse.Namespace) -> dict | None:
    try:
        target_metrics = _parse_json(args.target_metrics, '--target-metrics', list)
    except (ValueError, TypeError) as exc:
        console.print(f'[red]{exc}[/red]')
        return None
    return {
        'slug': args.slug,
        'name': args.name,
        'description': args.description,
        'target_metrics': target_metrics,
        'owner_id': _uuid_str(args.owner_id),
    }


async def capability_create(config: RootConfig, args: argparse.Namespace) -> int:
    """Create a Capability via the Catalog API."""
    payload = _capability_payload(args)
    if payload is None:
        return 1
    return await _post_and_show(config, RESOURCES['capability'].api_path, payload)


async def capability_update(config: RootConfig, args: argparse.Namespace) -> int:
    """Create a new Capability version via the Catalog API."""
    payload = _capability_payload(args)
    if payload is None:
        return 1
    path = f'{RESOURCES["capability"].api_path}/{args.entity_id}/versions'
    return await _post_and_show(config, path, payload)


# --- ModelEndpoint: create/update -------------------------------------------


def _model_payload(args: argparse.Namespace) -> dict | None:
    return {
        'slug': args.slug,
        'name': args.name,
        'description': args.description,
        'protocol': args.protocol,
        'base_url': args.base_url,
        'model': args.model,
        'auth_binding_id': _uuid_str(args.auth_binding_id),
        'owner_id': _uuid_str(args.owner_id),
    }


async def model_create(config: RootConfig, args: argparse.Namespace) -> int:
    """Create a ModelEndpoint via the Catalog API."""
    payload = _model_payload(args)
    return await _post_and_show(config, RESOURCES['model'].api_path, payload)


async def model_update(config: RootConfig, args: argparse.Namespace) -> int:
    """Create a new ModelEndpoint version via the Catalog API."""
    payload = _model_payload(args)
    path = f'{RESOURCES["model"].api_path}/{args.entity_id}/versions'
    return await _post_and_show(config, path, payload)


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
    return {
        'slug': args.slug,
        'name': args.name,
        'description': args.description,
        'layer': args.layer,
        'model_binding_id': _uuid_str(args.model_binding_id),
        'llm_config': llm_config,
        'prompt': args.prompt,
        'memory_scope': args.memory_scope,
        'permission_boundary': permission_boundary,
        'owner_id': _uuid_str(args.owner_id),
    }


async def agent_create(config: RootConfig, args: argparse.Namespace) -> int:
    """Create an Agent via the Catalog API."""
    payload = _agent_payload(args)
    if payload is None:
        return 1
    return await _post_and_show(config, RESOURCES['agent'].api_path, payload)


async def agent_update(config: RootConfig, args: argparse.Namespace) -> int:
    """Create a new Agent version via the Catalog API."""
    payload = _agent_payload(args)
    if payload is None:
        return 1
    path = f'{RESOURCES["agent"].api_path}/{args.entity_id}/versions'
    return await _post_and_show(config, path, payload)


# --- argparse wiring ---------------------------------------------------


def _add_owner_id_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        '--owner-id',
        dest='owner_id',
        type=uuid.UUID,
        default=None,
        help='Owning Principal, defaults to the caller',
    )


def _add_capability_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('slug', help='URL-safe unique slug')
    parser.add_argument('name', help='Human-readable name')
    parser.add_argument('--description', default=None, help='Free-text description')
    parser.add_argument(
        '--target-metrics',
        dest='target_metrics',
        default='[]',
        help='Target metrics as a JSON array of objects, defaults to []',
    )
    _add_owner_id_arg(parser)


def _add_model_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('slug', help='URL-safe unique slug')
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
    _add_owner_id_arg(parser)


def _add_agent_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('slug', help='URL-safe unique slug')
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
    parser.add_argument('--prompt', required=True, help='The versioned system prompt')
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
    _add_owner_id_arg(parser)


def _add_generic_verbs(sub: argparse._SubParsersAction, spec: ResourceSpec) -> None:
    """The four verbs identical in shape across every resource: list, show,
    versions, transition. create/update stay resource-specific (see the
    `_add_*_fields` helpers above) since their payload shape differs."""
    list_parser = sub.add_parser('list', help=f'List {spec.plural}')
    list_parser.add_argument(
        '--lifecycle-state',
        dest='lifecycle_state',
        default=None,
        choices=[s.value for s in LifecycleState],
        help='Filter by lifecycle state',
    )
    list_parser.add_argument('--slug', default=None, help='Filter by exact slug')
    list_parser.add_argument(
        '--limit', type=int, default=50, help='Max results, defaults to 50'
    )
    list_parser.add_argument(
        '--offset', type=int, default=0, help='Pagination offset, defaults to 0'
    )
    list_parser.set_defaults(func=functools.partial(resource_list, spec))

    show_parser = sub.add_parser('show', help=f'Show {spec.article} {spec.title}')
    show_parser.add_argument('entity_id', type=uuid.UUID)
    show_parser.add_argument(
        '--version',
        type=int,
        default=None,
        help='Show a specific version instead of the current one',
    )
    show_parser.set_defaults(func=functools.partial(resource_show, spec))

    versions_parser = sub.add_parser(
        'versions', help=f'List every version of {spec.article} {spec.title}'
    )
    versions_parser.add_argument('entity_id', type=uuid.UUID)
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


def add_catalog_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register `loom capability|model|agent {create,update,list,show,
    versions,transition}` -- called once from `cli/main.py` so resource
    knowledge (payload shape, list columns, API path) stays here rather
    than growing `main.py`'s own argparse setup by 3 resources x 6 verbs."""
    _add_capability_parsers(subparsers)
    _add_model_parsers(subparsers)
    _add_agent_parsers(subparsers)
