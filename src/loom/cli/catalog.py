import argparse
import json
import time
import typing

import yaml

from loom.catalog_client import CatalogApiError, CatalogClient
from loom.config import RootConfig


def _access_token(config: RootConfig) -> str | None:
    """The cached CLI session's access token, or None (after printing why)
    if there isn't a usable one -- the same "not logged in"/"expired" story
    as `loom auth status`, so a create command fails with an actionable
    message instead of an opaque 401 from the API."""
    if config.auth.session.access_token is None:
        print('Not logged in. Run `loom auth login`.')
        return None
    remaining = (config.auth.session.expires_at or 0) - int(time.time())
    if remaining <= 0:
        print('Session expired. Run `loom auth login`.')
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


async def _create(config: RootConfig, path: str, payload: dict) -> int:
    """Shared POST-and-print for every `loom {resource} create` command."""
    token = _access_token(config)
    if token is None:
        return 1
    client = CatalogClient(config.catalog.api_base_url, token)
    try:
        created = await client.post(path, payload)
    except CatalogApiError as exc:
        if exc.status_code == 401:
            print('Not authorized. Run `loom auth login` again.')
        else:
            print(f'{exc.status_code}: {exc.detail}')
        return 1
    print(yaml.dump(created, sort_keys=False))
    return 0


async def capability_create(config: RootConfig, args: argparse.Namespace) -> int:
    """Create a Capability via the Catalog API."""
    try:
        target_metrics = _parse_json(args.target_metrics, '--target-metrics', list)
    except (ValueError, TypeError) as exc:
        print(exc)
        return 1
    payload = {
        'slug': args.slug,
        'name': args.name,
        'description': args.description,
        'target_metrics': target_metrics,
        'owner_id': _uuid_str(args.owner_id),
    }
    return await _create(config, '/api/v1/capabilities', payload)


async def model_create(config: RootConfig, args: argparse.Namespace) -> int:
    """Create a ModelEndpoint via the Catalog API."""
    payload = {
        'slug': args.slug,
        'name': args.name,
        'description': args.description,
        'protocol': args.protocol,
        'base_url': args.base_url,
        'model': args.model,
        'auth_binding_id': _uuid_str(args.auth_binding_id),
        'owner_id': _uuid_str(args.owner_id),
    }
    return await _create(config, '/api/v1/model-endpoints', payload)


async def agent_create(config: RootConfig, args: argparse.Namespace) -> int:
    """Create an Agent via the Catalog API."""
    try:
        llm_config = _parse_json(args.llm_config, '--llm-config', dict)
        permission_boundary = _parse_json(
            args.permission_boundary, '--permission-boundary', dict
        )
    except (ValueError, TypeError) as exc:
        print(exc)
        return 1
    payload = {
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
    return await _create(config, '/api/v1/agents', payload)
