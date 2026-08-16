import pytest


@pytest.mark.asyncio
async def test_all_routers_are_mounted(api_client, fake_principal):
    tenant_id = fake_principal.tenant_id
    for path in (
        f'/api/v1/tenants/{tenant_id}/capabilities',
        f'/api/v1/tenants/{tenant_id}/agents',
        f'/api/v1/tenants/{tenant_id}/skills',
        f'/api/v1/tenants/{tenant_id}/tools',
        f'/api/v1/tenants/{tenant_id}/datasources',
        f'/api/v1/tenants/{tenant_id}/dataproducts',
        f'/api/v1/tenants/{tenant_id}/model-endpoints',
        '/api/v1/tenants',
        f'/api/v1/tenants/{tenant_id}/principals',
        f'/api/v1/tenants/{tenant_id}/environments',
    ):
        response = await api_client.get(path)
        assert response.status_code == 200, f'{path} returned {response.status_code}'


@pytest.mark.asyncio
async def test_missing_token_returns_401(api_client):
    # A request through the raw ASGI app (no dependency override) must 401.
    from httpx import ASGITransport, AsyncClient

    from loom.api.catalog.main import create_app
    from loom.config import RootConfig
    from loom.model.engine import get_async_session_factory

    config = RootConfig(config_path='/dev/null')
    real_app = create_app(config)
    # Populate only what an unauthenticated request path touches: the OAuth2
    # scheme rejects the missing Authorization header before token_validator
    # is ever used, so running the full lifespan (which eagerly resolves the
    # discovery document over the network) is unnecessary and would fail
    # against the default empty issuer. `create_app` itself doesn't hit the
    # network either -- the empty issuer above means it skips discovery
    # entirely, same as this comment always assumed.
    real_app.state.session_factory = get_async_session_factory(config.database)
    transport = ASGITransport(app=real_app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        response = await client.get(
            '/api/v1/tenants/00000000-0000-0000-0000-000000000000/capabilities'
        )
    assert response.status_code in (401, 403)


def test_openapi_advertises_interactive_idp_login(monkeypatch):
    """Swagger's Authorize flow points at the configured IDP, per-op locked."""
    from loom.api.catalog import security as security_module
    from loom.api.catalog.main import create_app
    from loom.api.catalog.security import OidcDiscoveryDocument
    from loom.config import RootConfig

    # `create_app` resolves the discovery document synchronously (the live
    # `test_swagger_login.py` integration test relies on that -- it checks
    # `app.openapi()` right after `create_app()`, no lifespan run), so a
    # fake issuer needs the network call faked out too, not just left to
    # fail against `idp.example`. Patched on `security_module` (where
    # `discover_and_resolve_issuer` actually calls it), not `main_module` --
    # `main.py` no longer imports `discover_oidc` directly.
    fake_discovery = OidcDiscoveryDocument(
        issuer='https://idp.example/realms/loom',
        authorization_endpoint='https://idp.example/realms/loom/protocol/openid-connect/auth',
        token_endpoint='https://idp.example/realms/loom/protocol/openid-connect/token',
        jwks_uri='https://idp.example/realms/loom/protocol/openid-connect/certs',
    )
    monkeypatch.setattr(
        security_module, 'discover_oidc', lambda discovery_url: fake_discovery
    )

    config = RootConfig(config_path='/dev/null')
    config.auth.issuer = 'https://idp.example/realms/loom'
    config.auth.swagger_client_id = 'loom-catalog-api-swagger'
    app = create_app(config)

    assert app.swagger_ui_init_oauth == {
        'clientId': 'loom-catalog-api-swagger',
        'usePkceWithAuthorizationCodeGrant': True,
    }

    schema = app.openapi()
    scheme = schema['components']['securitySchemes']['OAuth2AuthorizationCodeBearer']
    flow = scheme['flows']['authorizationCode']
    assert flow['authorizationUrl'] == (
        'https://idp.example/realms/loom/protocol/openid-connect/auth'
    )
    assert flow['tokenUrl'] == (
        'https://idp.example/realms/loom/protocol/openid-connect/token'
    )

    capabilities_get = schema['paths']['/api/v1/tenants/{tenant_id}/capabilities'][
        'get'
    ]
    assert capabilities_get['security'] == [{'OAuth2AuthorizationCodeBearer': []}]


def test_openapi_omits_swagger_login_when_swagger_client_unset():
    """An unconfigured Swagger client disables the button rather than
    breaking it."""
    from loom.api.catalog.main import create_app
    from loom.config import RootConfig

    app = create_app(RootConfig(config_path='/dev/null'))
    assert app.swagger_ui_init_oauth is None


def test_docs_redirect_path_matches_what_the_cli_registers():
    """`idp register`'s Swagger UI step builds
    `{api_base_url}/docs/oauth2-redirect` as the Keycloak redirect URI --
    pin it against FastAPI's own served path so the two can't silently
    drift and fail as `invalid_redirect_uri` at the IDP instead of a caught
    test."""
    from loom.api.catalog.main import create_app
    from loom.config import RootConfig

    app = create_app(RootConfig(config_path='/dev/null'))
    assert app.swagger_ui_oauth2_redirect_url == '/docs/oauth2-redirect'
