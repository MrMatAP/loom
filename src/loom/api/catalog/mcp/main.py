from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from loom import default_config_path
from loom.config import RootConfig
from loom.persistence.engine import get_async_session_factory

from ..security import TokenValidator, discover_and_resolve_issuer
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
    #
    # stateless_http=True: every tool call already re-resolves the caller's
    # principal from the Authorization header on that request (see
    # `server.py`'s `_authenticated_principal`), so no per-call state needs
    # to survive between requests. Without this, fastmcp pins an MCP
    # session to whichever server process handled its `initialize` call --
    # fine for a single process, but it defeats horizontal scaling behind a
    # Service/LoadBalancer, where a session's later requests can land on a
    # different replica with no memory of it. Verified against a real
    # Streamable HTTP round trip in
    # `tests/api/test_mcp_main.py::test_mcp_tool_call_over_real_http_reads_the_authorization_header`.
    mcp_asgi_app = mcp.http_app(stateless_http=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Deferred to lifespan, not built at create_app()/import time,
        # because `discover_and_resolve_issuer` resolves the IdP's OIDC
        # discovery document (and reconciles `config.auth.issuer` against
        # it -- see that function's docstring) over the network -- same
        # reasoning as api.catalog.main's own lifespan.
        #
        # The MCP server is registered as its own resource-server client
        # (see `cli.idp.idp_register`), separate from the RESTful API's --
        # tokens must carry `mcp_audience` in `aud`, not `audience`, so the
        # validator is built off a copy of `config.auth` with `audience`
        # overridden rather than sharing the REST API's `TokenValidator`.
        # `discover_and_resolve_issuer` is called against the *original*
        # `config`, not the copy below -- it persists `config.auth.issuer`
        # via `config.save()`, which a `model_copy()` can't do.
        state.session_factory = get_async_session_factory(config.database)
        discovery = discover_and_resolve_issuer(config)
        mcp_auth_config = config.auth.model_copy(
            update={'audience': config.auth.mcp_audience}
        )
        state.token_validator = TokenValidator(mcp_auth_config, discovery)
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


def run() -> None:
    """Entry point for the loom-catalog-mcp console script.

    `app` is built here, not as a module-level singleton -- see the
    identical reasoning in `api.catalog.main.run`. This module's own
    discovery is already deferred to the lifespan rather than `create_app`,
    so a plain import doesn't hit the network today, but constructing a
    real app from the local machine's config as an import-time side effect
    is the same latent trap either way.
    """
    app = create_app(RootConfig.load(config_path=default_config_path()))
    uvicorn.run(app, host='0.0.0.0', port=8100)
