from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from loom import __default_config_path__
from loom.config import RootConfig
from loom.model.engine import get_async_session_factory

from ..security import TokenValidator
from .server import McpState, create_mcp_server


def create_app(config: RootConfig) -> FastAPI:
    """Build the Catalog MCP FastAPI application from configuration -- an
    alternative interface onto the same Catalog use-cases `api.catalog.main`
    exposes over REST (see `mcp/server.py`)."""
    state = McpState()
    mcp = create_mcp_server(state)
    # `path` defaults to fastmcp.settings.streamable_http_path ("/mcp");
    # mounting the returned sub-app at "/" below keeps that as the final
    # client-visible path rather than nesting it under a second prefix.
    mcp_asgi_app = mcp.http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Deferred to lifespan, not built at create_app()/import time,
        # because TokenValidator.__init__ resolves the JWKS URI over the
        # network -- same reasoning as api.catalog.main's own lifespan.
        state.session_factory = get_async_session_factory(config.database)
        state.token_validator = TokenValidator(config.auth)
        # mcp_asgi_app's own lifespan starts its Streamable HTTP session
        # manager; without running it under the parent's lifespan here,
        # every tool call would fail with "Task group is not initialized".
        async with mcp_asgi_app.lifespan(app):
            yield
        await state.session_factory.kw['bind'].dispose()

    app = FastAPI(title='Loom Catalog MCP', lifespan=lifespan)

    @app.get('/healthz')
    async def healthz() -> dict:
        return {'status': 'ok'}

    app.mount('/', mcp_asgi_app)
    return app


app = create_app(RootConfig.load(config_path=__default_config_path__))


def run() -> None:
    """Entry point for the loom-catalog-mcp console script."""
    uvicorn.run(app, host='0.0.0.0', port=8100)
