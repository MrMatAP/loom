"""Single source of truth for the Catalog scope/role vocabulary."""

CONTENT_RESOURCES = (
    'capability',
    'agent',
    'skill',
    'tool',
    'datasource',
    'dataproduct',
)
CONTENT_ACTIONS = ('read', 'write', 'transition')
PLATFORM_RESOURCES = ('tenant', 'principal', 'environment')
PLATFORM_ACTIONS = ('read', 'write')


def _scopes_for(resources: tuple[str, ...], action: str) -> frozenset[str]:
    return frozenset(f'catalog:{resource}:{action}' for resource in resources)


def content_scopes() -> frozenset[str]:
    """All catalog:{resource}:{read,write,transition} content scopes."""
    return frozenset().union(
        *(_scopes_for(CONTENT_RESOURCES, action) for action in CONTENT_ACTIONS)
    )


def platform_scopes() -> frozenset[str]:
    """All catalog:{resource}:{read,write} platform scopes."""
    return frozenset().union(
        *(_scopes_for(PLATFORM_RESOURCES, action) for action in PLATFORM_ACTIONS)
    )


ROLE_BUNDLES: dict[str, frozenset[str]] = {
    'catalog-viewer': _scopes_for(CONTENT_RESOURCES, 'read'),
    'catalog-editor': (
        _scopes_for(CONTENT_RESOURCES, 'read') | _scopes_for(CONTENT_RESOURCES, 'write')
    ),
    'catalog-approver': (
        _scopes_for(CONTENT_RESOURCES, 'read')
        | _scopes_for(CONTENT_RESOURCES, 'transition')
    ),
    'catalog-admin': content_scopes(),
    'catalog-platform-admin': content_scopes() | platform_scopes(),
}
