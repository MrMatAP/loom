from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from loom import __version__, default_config_path
from loom.config import RootConfig
from loom.persistence.engine import get_async_session_factory

from .agent.router import router as agent_router
from .capability.router import router as capability_router
from .dataproduct.router import router as dataproduct_router
from .datasource.router import router as datasource_router
from .dependencies import oauth2_scheme
from .environment.router import router as environment_router
from .exceptions import register_exception_handlers
from .model_endpoint.router import router as model_endpoint_router
from .principal.router import router as principal_router
from .security import TokenValidator, discover_and_resolve_issuer
from .skill.router import router as skill_router
from .tenant.router import router as tenant_router
from .tool.router import router as tool_router

ROUTERS = (
    capability_router,
    agent_router,
    skill_router,
    tool_router,
    datasource_router,
    dataproduct_router,
    model_endpoint_router,
    tenant_router,
    principal_router,
    environment_router,
)


def create_app(config: RootConfig) -> FastAPI:
    """Build the Catalog FastAPI application from configuration."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.session_factory = get_async_session_factory(config.database)
        app.state.token_validator = TokenValidator(config.auth, discovery)
        yield
        await app.state.session_factory.kw['bind'].dispose()

    # oauth2_scheme is a module-level singleton shared by every route's
    # dependency tree, so its authorizationCode flow URLs are filled in here
    # from the running config rather than at import time.
    #
    # Resolved once, here, rather than separately in the lifespan too:
    # `discovery` is also what `TokenValidator` above reads its `jwks_uri`
    # from, so a configured issuer costs exactly one discovery-document
    # fetch, not two. `discover_and_resolve_issuer` also reconciles
    # `config.auth.issuer` against what the discovery document reports --
    # see its docstring -- and raises immediately (failing this whole
    # import, before `run()` ever calls `uvicorn.run`) if the discovery
    # endpoint doesn't respond or the stored issuer disagrees with it. An
    # entirely unconfigured issuer/discovery_url (e.g. tests that never
    # intend to exercise auth) must still let the app construct, so this
    # stays `None` -- and the flow URLs stay unset -- rather than fetching
    # against an empty base URL.
    flow = oauth2_scheme.model.flows.authorizationCode
    discovery = discover_and_resolve_issuer(config)
    if discovery is not None:
        flow.authorizationUrl = discovery.authorization_endpoint
        flow.tokenUrl = discovery.token_endpoint

    swagger_ui_init_oauth = None
    if config.auth.swagger_client_id:
        swagger_ui_init_oauth = {
            'clientId': config.auth.swagger_client_id,
            'usePkceWithAuthorizationCodeGrant': True,
        }

    app = FastAPI(
        title='Loom Catalog Service',
        lifespan=lifespan,
        swagger_ui_init_oauth=swagger_ui_init_oauth,
        version=__version__
    )
    register_exception_handlers(app)

    @app.get('/healthz')
    async def healthz() -> dict:
        return {'status': 'ok'}

    for router in ROUTERS:
        app.include_router(router, prefix='/api/v1')

    return app


def run() -> None:
    """Entry point for the loom-catalog-api console script.

    `app` is built here, not as a module-level singleton, so that plain
    `import loom.api.catalog.main` (e.g. tests that only want
    `create_app` to build their own app against test config) never
    resolves OIDC discovery against the real configured issuer. Nothing
    else needs the module-level object: nothing imports `main:app` as a
    string (`uvicorn.run` below is passed the object directly), so
    deferring construction to here is free.
    """
    app = create_app(RootConfig.load(config_path=default_config_path()))
    uvicorn.run(app, host='0.0.0.0', port=8000)
