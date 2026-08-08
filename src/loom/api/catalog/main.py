from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from loom import __default_config_path__
from loom.config import RootConfig
from loom.model.engine import get_async_session_factory

from .exceptions import register_exception_handlers
from .security import TokenValidator


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

    from .capability.router import router as capability_router

    app.include_router(capability_router, prefix='/api/v1')

    @app.get('/healthz')
    async def healthz() -> dict:
        return {'status': 'ok'}

    return app


app = create_app(RootConfig.load(config_path=__default_config_path__))


def run() -> None:
    """Entry point for the loom-catalog-api console script."""
    uvicorn.run(app, host='0.0.0.0', port=8000)
