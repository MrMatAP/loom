from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from loom import __default_config_path__
from loom.config import RootConfig
from loom.model.engine import get_async_session_factory

from .agent.router import router as agent_router
from .capability.router import router as capability_router
from .dataproduct.router import router as dataproduct_router
from .datasource.router import router as datasource_router
from .environment.router import router as environment_router
from .exceptions import register_exception_handlers
from .principal.router import router as principal_router
from .security import TokenValidator
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
    tenant_router,
    principal_router,
    environment_router,
)


def create_app(config: RootConfig) -> FastAPI:
    """Build the Catalog FastAPI application from configuration."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.session_factory = get_async_session_factory(config.database)
        app.state.token_validator = TokenValidator(config.auth)
        yield
        await app.state.session_factory.kw['bind'].dispose()

    app = FastAPI(title='Loom Catalog Service', lifespan=lifespan)
    register_exception_handlers(app)

    @app.get('/healthz')
    async def healthz() -> dict:
        return {'status': 'ok'}

    for router in ROUTERS:
        app.include_router(router, prefix='/api/v1')

    return app


app = create_app(RootConfig.load(config_path=__default_config_path__))


def run() -> None:
    """Entry point for the loom-catalog-api console script."""
    uvicorn.run(app, host='0.0.0.0', port=8000)
