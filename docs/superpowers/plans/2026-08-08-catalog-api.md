# Catalog REST API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Catalog component — a standalone async FastAPI service fronting the Catalog slice of the registry data model (`src/loom/model/`), with JWT/OIDC auth, scope-based authorization, a platform-bootstrap tier (Tenant/Principal/Environment), and a `loom idp register-client` CLI command that declares the role vocabulary in Keycloak.

**Architecture:** `Router → Service (use-cases) → Repository → ORM`, one module per aggregate under `src/loom/api/catalog/`. `loom.model` ORM classes stay the domain model. Async SQLAlchemy throughout the API layer (new async engine alongside the existing sync one used by CLI/Alembic). Auth is OIDC Bearer JWT validated via JWKS; `catalog:{resource}:{action}` scopes enforced per-route; content-tier writes always derive `tenant_id`/`created_by_id` from the resolved principal, never the client.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async (`AsyncSession`), PyJWT, httpx, pytest + pytest-asyncio, Keycloak (via a provider-agnostic interface).

## Global Constraints

- Formatter: `ruff format` — single quotes, 88-char lines, 4-space indent, Python 3.14 target.
- Import order: stdlib, then third-party, then local (`loom.*`), blank line between groups, in every file. `pyproject.toml` already has `src = ["src/loom", "tests"]` and `known-first-party = ["loom"]` configured — trust `ruff format`/`ruff check` to group correctly.
- Docstrings: short, single-line, ≤88 characters **including the 4-space indent and both triple-quote sets**. Verify every docstring's actual length with `awk '{print length}'` before committing — do not estimate. This bit the previous plan repeatedly; do not repeat it.
- Type hints on all function signatures.
- **Scope discipline, strictly enforced**: run `ruff format`/`ruff check` ONLY on the exact files each task creates or modifies — never bare across `src/loom` or `tests`. Before committing, run `git status`/`git diff --stat` and confirm only the task's own files are staged; stage explicitly by path, never `git add -A`/`git add .`.
- Never commit build/test artifacts (`.coverage`, stray files, etc.).
- Reports must contain only genuine, copy-pasted command output — never reconstructed.
- Content-tier request bodies (Capability/Agent/Skill/Tool/DataSource/DataProduct) never accept `tenant_id` or `created_by_id` — always derived from the resolved `AuthenticatedPrincipal`. `owner_id` may be client-specified, defaulting to the caller.
- Platform-tier request bodies (Principal/Environment) DO accept a client-specified `tenant_id` — this is the one deliberate exception, documented in the design spec.
- Every "exactly one of N" polymorphic reference in the ORM already exists from the prior plan (`exactly_one_of` in `src/loom/model/base.py`) — nothing in this plan adds new ones; services just populate the existing FK columns correctly.
- No SQLAlchemy `relationship()` — repositories use explicit `select()` queries.
- All new async code uses `AsyncSession`/`create_async_engine` — the existing sync engine/session helpers in `src/loom/model/engine.py` are untouched (still used by `loom db` / Alembic).
- Async DB tests use `sqlite+aiosqlite:///:memory:` (new `aiosqlite` dev dependency) — production uses `postgresql+psycopg` async via psycopg3's native asyncio support, same URL scheme as the sync engine.
- SQLAlchemy's async engine requires `greenlet` at runtime (not pulled in by plain `sqlalchemy>=2.0.36`) — added explicitly as its own dependency in Task 1, verified empirically before writing this plan (without it, `engine.begin()`/`AsyncSession` raise `ValueError: the greenlet library is required...`).
- To enable FK enforcement on an in-memory SQLite **async** engine in tests, register the `connect` event listener on `engine.sync_engine` (the specific engine instance), not globally on `sa.engine.Engine`, and do not `isinstance`-check `dbapi_connection` against `sqlite3.Connection` — the async dialect wraps the raw connection in an adapter that is not a `sqlite3.Connection` subclass, so that check silently fails. Verified empirically (see `tests/api/conftest.py` in Task 2).

---

## Task 1: Dependencies, AuthConfig, async engine

**Files:**
- Modify: `pyproject.toml`
- Create: `src/loom/config/auth_config.py`
- Modify: `src/loom/config/root_config.py`
- Modify: `src/loom/config/__init__.py`
- Modify: `src/loom/model/engine.py`
- Test: `tests/config/test_auth_config.py`
- Test: `tests/model/test_async_engine.py`

**Interfaces:**
- Produces: `loom.config.auth_config.AuthConfig(issuer, audience, jwks_uri, algorithms)`; `RootConfig.auth: AuthConfig`; `loom.model.engine.get_async_engine(config: DatabaseConfig) -> AsyncEngine`, `get_async_session_factory(config: DatabaseConfig) -> async_sessionmaker[AsyncSession]`.

- [ ] **Step 1: Add dependencies to `pyproject.toml`**

Add to `dependencies`:
```toml
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "pyjwt[crypto]>=2.9.0",
    "httpx>=0.27.0",
    "greenlet>=3.0.0",
```
Add to `[dependency-groups].dev`:
```toml
    "aiosqlite>=0.20.0",
```
Run: `uv sync`
Expected: resolves and installs without error.

- [ ] **Step 2: Write the failing tests**

```python
# tests/config/test_auth_config.py
from loom.config.auth_config import AuthConfig
from loom.config.root_config import RootConfig


def test_auth_config_defaults():
    config = AuthConfig(issuer='https://idp.example/realms/loom', audience='loom-catalog-api')
    assert config.algorithms == ['RS256']
    assert config.jwks_uri is None


def test_root_config_has_auth_section(tmp_path):
    root = RootConfig(config_path=tmp_path / 'config.yaml')
    assert root.auth.algorithms == ['RS256']
```

```python
# tests/model/test_async_engine.py
import sqlalchemy as sa
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from loom.config.database_config import DatabaseConfig
from loom.model.engine import get_async_engine, get_async_session_factory


def test_get_async_engine_builds_lazily_without_connecting():
    engine = get_async_engine(DatabaseConfig(host='unreachable-host'))
    assert isinstance(engine, AsyncEngine)
    assert engine.url.drivername == 'postgresql+psycopg'


@pytest.mark.asyncio
async def test_get_async_session_factory_can_query_sqlite(monkeypatch):
    monkeypatch.setattr(
        DatabaseConfig, 'dsn', property(lambda self: 'sqlite+aiosqlite:///:memory:')
    )
    config = DatabaseConfig()
    factory = get_async_session_factory(config)
    async with factory() as session:
        result = await session.execute(sa.text('SELECT 1'))
        assert result.scalar_one() == 1
    await factory.kw['bind'].dispose()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/config/test_auth_config.py tests/model/test_async_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.config.auth_config'`

- [ ] **Step 4: Implement**

```python
# src/loom/config/auth_config.py
from pydantic import Field

from .base import RootConfigAware


class AuthConfig(RootConfigAware):
    """OIDC settings for validating bearer tokens presented to the API."""

    issuer: str = Field(default='', description='OIDC issuer URL')
    audience: str = Field(default='', description='Expected token audience')
    jwks_uri: str | None = Field(default=None, description='JWKS URI, derived if unset')
    algorithms: list[str] = Field(default=['RS256'], description='Accepted JWT algorithms')
```

Modify `src/loom/config/root_config.py` — add the import and field, same pattern as `database`:

```python
from .auth_config import AuthConfig
```
```python
    auth: AuthConfig = Field(default_factory=AuthConfig)
```

(Insert alongside the existing `database: DatabaseConfig = Field(default_factory=DatabaseConfig)` field.)

Modify `src/loom/config/__init__.py` — add `from .auth_config import AuthConfig` alongside the existing `DatabaseConfig`/`RootConfig` exports.

Modify `src/loom/model/engine.py` — add after the existing sync `get_engine`/`get_session_factory`:

```python
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine, AsyncSession
```

```python
def get_async_engine(config: DatabaseConfig) -> AsyncEngine:
    """Build an async SQLAlchemy engine from database configuration."""
    return create_async_engine(config.dsn)


def get_async_session_factory(config: DatabaseConfig) -> async_sessionmaker[AsyncSession]:
    """Build an async session factory bound to an async engine from config."""
    return async_sessionmaker(bind=get_async_engine(config), expire_on_commit=False)
```

(Merge the new import into the existing import block at the top of `engine.py` rather than duplicating; keep stdlib/third-party/local grouping.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/config/test_auth_config.py tests/model/test_async_engine.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/loom/config/auth_config.py src/loom/config/root_config.py src/loom/config/__init__.py src/loom/model/engine.py tests/config/test_auth_config.py tests/model/test_async_engine.py
git commit -m "Add AuthConfig, wire into RootConfig, add async engine helpers"
```

---

## Task 2: Auth, app shell, shared infra, and test fixtures

**Files:**
- Create: `src/loom/idp/__init__.py`
- Create: `src/loom/idp/catalog_roles.py`
- Create: `src/loom/api/__init__.py`
- Create: `src/loom/api/catalog/__init__.py`
- Create: `src/loom/api/catalog/security.py`
- Create: `src/loom/api/catalog/exceptions.py`
- Create: `src/loom/api/catalog/db.py`
- Create: `src/loom/api/catalog/dependencies.py`
- Create: `src/loom/api/catalog/pagination.py`
- Create: `src/loom/api/catalog/lifecycle.py`
- Create: `src/loom/api/catalog/main.py`
- Create: `tests/api/__init__.py`
- Create: `tests/api/conftest.py`
- Test: `tests/api/test_security.py`
- Test: `tests/api/test_app.py`
- Test: `tests/api/test_db.py`
- Test: `tests/idp/__init__.py`
- Test: `tests/idp/test_catalog_roles.py`

**Interfaces:**
- Consumes: `AuthConfig`, `DatabaseConfig`, `get_async_session_factory` (Task 1).
- Produces: `loom.idp.catalog_roles.{content_scopes, platform_scopes, ROLE_BUNDLES}`;
  `loom.api.catalog.security.{AuthenticatedPrincipal, TokenValidator, resolve_jwks_uri, expand_claims_to_scopes}`;
  `loom.api.catalog.exceptions.{EntityNotFoundError, IllegalTransitionError, DomainValidationError, register_exception_handlers}`;
  `loom.api.catalog.db.flush_or_raise(session) -> None` (used by every aggregate's repository);
  `loom.api.catalog.dependencies.{get_session, get_current_token, get_current_principal, require_scopes}`;
  `loom.api.catalog.pagination.{Page, PaginationParams}`;
  `loom.api.catalog.lifecycle.{LEGAL_TRANSITIONS, is_legal_transition, TransitionRequest}`;
  `loom.api.catalog.main.create_app(config: RootConfig) -> FastAPI`, module-level `app`, `run()`.
  Test fixtures (`tests/api/conftest.py`): `async_engine`, `async_session_factory`, `async_session`,
  `fake_principal`, `api_client` (an `httpx.AsyncClient` with `get_session`/`get_current_token`/
  `get_current_principal` overridden) — every later task's API tests depend on these.

- [ ] **Step 1: Write the failing tests**

```python
# tests/idp/__init__.py
```

```python
# tests/idp/test_catalog_roles.py
from loom.idp.catalog_roles import ROLE_BUNDLES, content_scopes, platform_scopes


def test_content_scopes_has_18_entries():
    scopes = content_scopes()
    assert len(scopes) == 18
    assert 'catalog:capability:read' in scopes
    assert 'catalog:agent:transition' in scopes


def test_platform_scopes_has_6_entries():
    scopes = platform_scopes()
    assert len(scopes) == 6
    assert 'catalog:tenant:write' in scopes
    assert 'catalog:environment:read' in scopes


def test_role_bundles_are_correctly_layered():
    assert ROLE_BUNDLES['catalog-viewer'] < ROLE_BUNDLES['catalog-editor']
    assert ROLE_BUNDLES['catalog-editor'] <= ROLE_BUNDLES['catalog-admin']
    assert ROLE_BUNDLES['catalog-approver'] <= ROLE_BUNDLES['catalog-admin']
    assert ROLE_BUNDLES['catalog-admin'] == content_scopes()
    assert ROLE_BUNDLES['catalog-platform-admin'] == content_scopes() | platform_scopes()
```

```python
# tests/api/test_security.py
import time
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from loom.api.catalog.security import TokenValidator, expand_claims_to_scopes
from loom.config.auth_config import AuthConfig


def _make_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def test_token_validator_decodes_valid_token(monkeypatch):
    private_key, public_key = _make_rsa_keypair()
    jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk['kid'] = 'test-key'
    jwk['alg'] = 'RS256'
    monkeypatch.setattr('jwt.PyJWKClient.fetch_data', lambda self: {'keys': [jwk]})

    config = AuthConfig(
        issuer='https://idp.example/realms/loom',
        audience='loom-catalog-api',
        jwks_uri='https://idp.example/realms/loom/protocol/openid-connect/certs',
    )
    validator = TokenValidator(config)

    token = jwt.encode(
        {
            'sub': 'user-123',
            'tenant_id': str(uuid.uuid4()),
            'scope': 'catalog:agent:read',
            'iss': config.issuer,
            'aud': config.audience,
            'exp': int(time.time()) + 300,
        },
        private_key,
        algorithm='RS256',
        headers={'kid': 'test-key'},
    )

    claims = validator.decode(token)
    assert claims['sub'] == 'user-123'


def test_expand_claims_to_scopes_merges_scope_and_role_claims():
    scopes = expand_claims_to_scopes(
        {'scope': 'catalog:agent:read', 'roles': ['catalog-viewer']}
    )
    assert 'catalog:agent:read' in scopes
    assert 'catalog:capability:read' in scopes
```

```python
# tests/api/test_app.py
import pytest


@pytest.mark.asyncio
async def test_healthz_is_unauthenticated(api_client):
    response = await api_client.get('/healthz')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
```

```python
# tests/api/test_db.py
import pytest

from loom.api.catalog.db import flush_or_raise
from loom.api.catalog.exceptions import DomainValidationError
from loom.model.tenant import Tenant


@pytest.mark.asyncio
async def test_flush_or_raise_translates_integrity_error(async_session):
    async_session.add(Tenant(slug='dup', name='A'))
    await flush_or_raise(async_session)
    async_session.add(Tenant(slug='dup', name='B'))
    with pytest.raises(DomainValidationError):
        await flush_or_raise(async_session)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/idp/test_catalog_roles.py tests/api/test_security.py tests/api/test_app.py tests/api/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.idp'`

- [ ] **Step 3: Implement**

```python
# src/loom/idp/__init__.py
"""IDP-agnostic admin operations: client registration, role vocabulary."""
```

```python
# src/loom/idp/catalog_roles.py
"""Single source of truth for the Catalog scope/role vocabulary."""

CONTENT_RESOURCES = ('capability', 'agent', 'skill', 'tool', 'datasource', 'dataproduct')
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
        _scopes_for(CONTENT_RESOURCES, 'read') | _scopes_for(CONTENT_RESOURCES, 'transition')
    ),
    'catalog-admin': content_scopes(),
    'catalog-platform-admin': content_scopes() | platform_scopes(),
}
```

```python
# src/loom/api/__init__.py
"""HTTP API components fronting the loom registry."""
```

```python
# src/loom/api/catalog/__init__.py
"""The Catalog component: Capability/Agent/Skill/Tool/DataSource/DataProduct."""
```

```python
# src/loom/api/catalog/security.py
import dataclasses
import uuid

import httpx
import jwt
from jwt import PyJWKClient

from loom.config.auth_config import AuthConfig
from loom.idp.catalog_roles import ROLE_BUNDLES


@dataclasses.dataclass(frozen=True)
class AuthenticatedPrincipal:
    """The resolved identity and permissions behind a validated request."""

    principal_id: uuid.UUID
    tenant_id: uuid.UUID
    scopes: frozenset[str]


def resolve_jwks_uri(issuer: str) -> str:
    """Discover the JWKS URI from the issuer's OIDC discovery document."""
    response = httpx.get(f'{issuer}/.well-known/openid-configuration', timeout=10.0)
    response.raise_for_status()
    return response.json()['jwks_uri']


class TokenValidator:
    """Validates bearer JWTs against a JWKS-published signing key."""

    def __init__(self, config: AuthConfig) -> None:
        self._config = config
        jwks_uri = config.jwks_uri or resolve_jwks_uri(config.issuer)
        self._jwk_client = PyJWKClient(jwks_uri)

    def decode(self, token: str) -> dict:
        """Verify signature/exp/iss/aud and return the token's claims."""
        signing_key = self._jwk_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=self._config.algorithms,
            audience=self._config.audience,
            issuer=self._config.issuer,
        )


def expand_claims_to_scopes(claims: dict) -> frozenset[str]:
    """Expand a token's scope/roles claims into a flat scope set."""
    scopes: set[str] = set()
    scope_claim = claims.get('scope')
    if scope_claim:
        scopes.update(scope_claim.split())
    for role in claims.get('roles', []):
        scopes.update(ROLE_BUNDLES.get(role, {role}))
    return frozenset(scopes)
```

```python
# src/loom/api/catalog/exceptions.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class EntityNotFoundError(Exception):
    """Raised when a requested entity or version does not exist."""


class IllegalTransitionError(Exception):
    """Raised when a lifecycle transition is not structurally legal."""


class DomainValidationError(Exception):
    """Raised when a write violates a database-level invariant."""


def register_exception_handlers(app: FastAPI) -> None:
    """Map domain exceptions to a consistent JSON error envelope."""

    @app.exception_handler(EntityNotFoundError)
    async def _not_found(request: Request, exc: EntityNotFoundError) -> JSONResponse:
        del request
        content = {'error_code': 'not_found', 'message': str(exc)}
        return JSONResponse(status_code=404, content=content)

    @app.exception_handler(IllegalTransitionError)
    async def _illegal_transition(
        request: Request, exc: IllegalTransitionError
    ) -> JSONResponse:
        del request
        content = {'error_code': 'illegal_transition', 'message': str(exc)}
        return JSONResponse(status_code=409, content=content)

    @app.exception_handler(DomainValidationError)
    async def _validation(request: Request, exc: DomainValidationError) -> JSONResponse:
        del request
        content = {'error_code': 'validation_error', 'message': str(exc)}
        return JSONResponse(status_code=422, content=content)
```

```python
# src/loom/api/catalog/pagination.py
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

ItemT = TypeVar('ItemT')


class Page(BaseModel, Generic[ItemT]):
    """A paginated list response envelope."""

    items: list[ItemT]
    total: int
    limit: int
    offset: int


class PaginationParams:
    """Shared limit/offset query parameters for list endpoints."""

    def __init__(
        self,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> None:
        self.limit = limit
        self.offset = offset
```

```python
# src/loom/api/catalog/db.py
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .exceptions import DomainValidationError


async def flush_or_raise(session: AsyncSession) -> None:
    """Flush the session, translating IntegrityError into a domain error."""
    try:
        await session.flush()
    except sa.exc.IntegrityError as exc:
        await session.rollback()
        raise DomainValidationError(str(exc.orig)) from exc
```

```python
# src/loom/api/catalog/lifecycle.py
from pydantic import BaseModel

from loom.model.enums import LifecycleState

LEGAL_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.DRAFT: frozenset({LifecycleState.IN_REVIEW}),
    LifecycleState.IN_REVIEW: frozenset({LifecycleState.APPROVED, LifecycleState.DRAFT}),
    LifecycleState.APPROVED: frozenset({LifecycleState.PUBLISHED}),
    LifecycleState.PUBLISHED: frozenset({LifecycleState.DEPRECATED}),
    LifecycleState.DEPRECATED: frozenset({LifecycleState.RETIRED}),
    LifecycleState.RETIRED: frozenset(),
}


def is_legal_transition(current: LifecycleState, target: LifecycleState) -> bool:
    """Check whether target is a legal next state from current."""
    return target in LEGAL_TRANSITIONS.get(current, frozenset())


class TransitionRequest(BaseModel):
    """Request body for a lifecycle-transition endpoint, shared by all routers."""

    to_state: LifecycleState
```

```python
# src/loom/api/catalog/dependencies.py
import uuid
from collections.abc import AsyncGenerator

import jwt
import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from loom.model.tenant import Principal

from .security import AuthenticatedPrincipal, expand_claims_to_scopes

bearer_scheme = HTTPBearer(auto_error=True)


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped async session, committing on success."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_current_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """Validate the bearer token and return its decoded claims."""
    validator = request.app.state.token_validator
    try:
        return validator.decode(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f'Invalid token: {exc}') from exc


async def get_current_principal(
    claims: dict = Depends(get_current_token),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedPrincipal:
    """Resolve the token's tenant_id/sub claims to an internal Principal."""
    tenant_claim = claims.get('tenant_id')
    sub = claims.get('sub')
    if not tenant_claim or not sub:
        detail = 'Token missing tenant_id or sub claim'
        raise HTTPException(status_code=401, detail=detail)
    try:
        tenant_id = uuid.UUID(str(tenant_claim))
    except ValueError as exc:
        detail = 'Token tenant_id claim is not a UUID'
        raise HTTPException(status_code=401, detail=detail) from exc
    principal = await session.scalar(
        sa.select(Principal).where(
            Principal.tenant_id == tenant_id, Principal.external_id == sub
        )
    )
    if principal is None:
        detail = 'No principal provisioned for this identity'
        raise HTTPException(status_code=401, detail=detail)
    return AuthenticatedPrincipal(
        principal_id=principal.id,
        tenant_id=tenant_id,
        scopes=expand_claims_to_scopes(claims),
    )


def require_scopes(*required: str):
    """Dependency factory: 403s unless the token's scopes cover all required."""

    async def _check(claims: dict = Depends(get_current_token)) -> None:
        scopes = expand_claims_to_scopes(claims)
        missing = set(required) - scopes
        if missing:
            joined = ', '.join(sorted(missing))
            detail = f'Missing required scope(s): {joined}'
            raise HTTPException(status_code=403, detail=detail)

    return _check
```

```python
# src/loom/api/catalog/main.py
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

    @app.get('/healthz')
    async def healthz() -> dict:
        return {'status': 'ok'}

    return app


app = create_app(RootConfig.load(config_path=__default_config_path__))


def run() -> None:
    """Entry point for the loom-catalog-api console script."""
    uvicorn.run(app, host='0.0.0.0', port=8000)
```

```python
# tests/api/__init__.py
```

```python
# tests/api/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from loom.api.catalog.dependencies import get_current_principal, get_current_token, get_session
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.idp.catalog_roles import content_scopes, platform_scopes
from loom.model import agent  # noqa: F401
from loom.model import capability  # noqa: F401
from loom.model import dataproduct  # noqa: F401
from loom.model import datasource  # noqa: F401
from loom.model import environment  # noqa: F401
from loom.model import evaluation  # noqa: F401
from loom.model import governance  # noqa: F401
from loom.model import observability  # noqa: F401
from loom.model import skill  # noqa: F401
from loom.model import tool  # noqa: F401
from loom.model.base import Base
from loom.model.enums import PrincipalKind
from loom.model.tenant import Principal, Tenant

ALL_SCOPES = content_scopes() | platform_scopes()


@pytest_asyncio.fixture
async def async_engine():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    @sa.event.listens_for(engine.sync_engine, 'connect')
    def _enable_fk(dbapi_connection, connection_record) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session_factory(async_engine):
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def async_session(async_session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def fake_principal(async_session_factory) -> AuthenticatedPrincipal:
    """Real Tenant+Principal seeded in the DB, wrapped with every scope granted."""
    async with async_session_factory() as session:
        tenant = Tenant(slug='test-tenant', name='Test Tenant')
        session.add(tenant)
        await session.flush()
        principal = Principal(
            tenant_id=tenant.id,
            kind=PrincipalKind.USER,
            display_name='Test User',
            external_id='test-user',
        )
        session.add(principal)
        await session.commit()
        return AuthenticatedPrincipal(
            principal_id=principal.id, tenant_id=tenant.id, scopes=frozenset(ALL_SCOPES)
        )


@pytest_asyncio.fixture
async def api_client(
    async_session_factory, fake_principal
) -> AsyncGenerator[AsyncClient, None]:
    from loom.api.catalog.main import create_app
    from loom.config import RootConfig

    app = create_app(RootConfig(config_path='/dev/null'))

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_token] = lambda: {
        'sub': 'test-user',
        'tenant_id': str(fake_principal.tenant_id),
    }
    app.dependency_overrides[get_current_principal] = lambda: fake_principal

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        client.app = app  # exposes the app so tests can re-override a dependency
        yield client
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/idp/test_catalog_roles.py tests/api/test_security.py tests/api/test_app.py tests/api/test_db.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/idp src/loom/api tests/idp tests/api
git commit -m "Add auth infra, app shell, and async API test fixtures"
```

---

## Task 3: Capability module (Router → Service → Repository)

This is the template task — every later content-aggregate task (4-8) follows
this exact structure (`add`/`save` split in the repository, `flush_or_raise`
for IntegrityError translation, version-scoped sub-resources, the same
router dependency shape). Read it fully even if a later task looks similar;
each later task still needs its own complete, correct code.

**Files:**
- Create: `src/loom/api/catalog/capability/__init__.py`
- Create: `src/loom/api/catalog/capability/schemas.py`
- Create: `src/loom/api/catalog/capability/repository.py`
- Create: `src/loom/api/catalog/capability/service.py`
- Create: `src/loom/api/catalog/capability/router.py`
- Create: `tests/api/catalog/__init__.py`
- Test: `tests/api/catalog/test_capability.py`

**Interfaces:**
- Consumes: `flush_or_raise` (Task 2 `db.py`), `EntityNotFoundError`/`IllegalTransitionError` (Task 2 `exceptions.py`), `is_legal_transition`/`TransitionRequest` (Task 2 `lifecycle.py`), `get_session`/`get_current_principal`/`require_scopes` (Task 2 `dependencies.py`), `Page`/`PaginationParams` (Task 2 `pagination.py`), `Capability`/`CapabilityRealization` ORM classes and `CapabilityRead`/`CapabilityRealizationRead` schemas (prior plan's `loom.model`).
- Produces: `router` (an `APIRouter`, mounted in Task 10); `CapabilityService`, `CapabilityRepository` (reused directly by nothing else, but the pattern is what Tasks 4-8 replicate).

- [ ] **Step 1: Write the failing tests**

```python
# src/loom/api/catalog/capability/__init__.py
```

```python
# tests/api/catalog/__init__.py
```

```python
# tests/api/catalog/test_capability.py
import pytest

from loom.model.agent import Agent
from loom.model.enums import Layer, MemoryScope, RealizingEntityType


@pytest.mark.asyncio
async def test_capability_full_lifecycle(api_client):
    create_resp = await api_client.post(
        '/capabilities',
        json={'slug': 'triage', 'name': 'Ticket Triage', 'target_metrics': []},
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    entity_id = created['entity_id']
    assert created['version'] == 1
    assert created['lifecycle_state'] == 'draft'

    get_resp = await api_client.get(f'/capabilities/{entity_id}')
    assert get_resp.status_code == 200
    assert get_resp.json()['slug'] == 'triage'

    list_resp = await api_client.get('/capabilities')
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body['total'] == 1
    assert body['items'][0]['entity_id'] == entity_id

    version_resp = await api_client.post(
        f'/capabilities/{entity_id}/versions',
        json={'slug': 'triage', 'name': 'Ticket Triage v2', 'target_metrics': []},
    )
    assert version_resp.status_code == 201
    v2 = version_resp.json()
    assert v2['version'] == 2
    assert v2['lifecycle_state'] == 'draft'

    versions_resp = await api_client.get(f'/capabilities/{entity_id}/versions')
    assert versions_resp.status_code == 200
    assert len(versions_resp.json()) == 2

    old_version_resp = await api_client.get(f'/capabilities/{entity_id}/versions/1')
    assert old_version_resp.status_code == 200
    assert old_version_resp.json()['name'] == 'Ticket Triage'

    current_resp = await api_client.get(f'/capabilities/{entity_id}')
    assert current_resp.json()['version'] == 2

    transition_resp = await api_client.post(
        f'/capabilities/{entity_id}/versions/2/transitions',
        json={'to_state': 'in_review'},
    )
    assert transition_resp.status_code == 200
    assert transition_resp.json()['lifecycle_state'] == 'in_review'


@pytest.mark.asyncio
async def test_illegal_transition_returns_409(api_client):
    create_resp = await api_client.post(
        '/capabilities', json={'slug': 'skip', 'name': 'Skip Ahead', 'target_metrics': []}
    )
    entity_id = create_resp.json()['entity_id']

    resp = await api_client.post(
        f'/capabilities/{entity_id}/versions/1/transitions',
        json={'to_state': 'published'},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_transition_on_stale_version_returns_409(api_client):
    create_resp = await api_client.post(
        '/capabilities', json={'slug': 'stale', 'name': 'Stale', 'target_metrics': []}
    )
    entity_id = create_resp.json()['entity_id']
    await api_client.post(
        f'/capabilities/{entity_id}/versions',
        json={'slug': 'stale', 'name': 'Stale v2', 'target_metrics': []},
    )

    resp = await api_client.post(
        f'/capabilities/{entity_id}/versions/1/transitions',
        json={'to_state': 'in_review'},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_capability_not_found_returns_404(api_client):
    resp = await api_client.get('/capabilities/00000000-0000-0000-0000-000000000000')
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_realizations_are_version_scoped(api_client, fake_principal, async_session):
    agent = Agent(
        tenant_id=fake_principal.tenant_id,
        owner_id=fake_principal.principal_id,
        created_by_id=fake_principal.principal_id,
        slug='triage-agent',
        name='Triage Agent',
        layer=Layer.BUSINESS_OPS,
        llm_config={},
        prompt='p',
        memory_scope=MemoryScope.NONE,
    )
    async_session.add(agent)
    await async_session.commit()

    create_resp = await api_client.post(
        '/capabilities', json={'slug': 'realize', 'name': 'Realize Me', 'target_metrics': []}
    )
    entity_id = create_resp.json()['entity_id']

    add_resp = await api_client.post(
        f'/capabilities/{entity_id}/versions/1/realizations',
        json={
            'realizing_entity_type': RealizingEntityType.AGENT.value,
            'realizing_agent_id': str(agent.id),
            'contribution_weight': '1.0',
        },
    )
    assert add_resp.status_code == 201

    list_resp = await api_client.get(f'/capabilities/{entity_id}/versions/1/realizations')
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1


@pytest.mark.asyncio
async def test_missing_scope_returns_403(api_client, fake_principal):
    from loom.api.catalog.dependencies import get_current_principal
    from loom.api.catalog.security import AuthenticatedPrincipal

    api_client.app.dependency_overrides[get_current_principal] = lambda: AuthenticatedPrincipal(
        principal_id=fake_principal.principal_id,
        tenant_id=fake_principal.tenant_id,
        scopes=frozenset({'catalog:capability:read'}),
    )
    resp = await api_client.post(
        '/capabilities', json={'slug': 'nope', 'name': 'Nope', 'target_metrics': []}
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/api/catalog/test_capability.py -v`
Expected: FAIL — `404 Not Found` for `/capabilities` (router not mounted / module doesn't exist)

- [ ] **Step 3: Implement**

```python
# src/loom/api/catalog/capability/schemas.py
import decimal
import uuid

from pydantic import BaseModel

from loom.model.enums import RealizingEntityType


class CapabilityCreateRequest(BaseModel):
    slug: str
    name: str
    description: str | None = None
    target_metrics: list[dict] = []
    owner_id: uuid.UUID | None = None


class CapabilityRealizationCreateRequest(BaseModel):
    realizing_entity_type: RealizingEntityType
    realizing_agent_id: uuid.UUID | None = None
    realizing_skill_id: uuid.UUID | None = None
    realizing_tool_id: uuid.UUID | None = None
    contribution_weight: decimal.Decimal
```

```python
# src/loom/api/catalog/capability/repository.py
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.model.capability import Capability, CapabilityRealization
from loom.model.enums import LifecycleState


class CapabilityRepository:
    """Async persistence access for Capability, scoped by tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> Capability | None:
        return await self._session.scalar(
            sa.select(Capability).where(
                Capability.tenant_id == tenant_id,
                Capability.entity_id == entity_id,
                Capability.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Capability | None:
        return await self._session.scalar(
            sa.select(Capability).where(
                Capability.tenant_id == tenant_id,
                Capability.entity_id == entity_id,
                Capability.version == version,
            )
        )

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[Capability]:
        result = await self._session.scalars(
            sa.select(Capability)
            .where(Capability.tenant_id == tenant_id, Capability.entity_id == entity_id)
            .order_by(Capability.version)
        )
        return list(result)

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Capability], int]:
        stmt = sa.select(Capability).where(
            Capability.tenant_id == tenant_id, Capability.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(Capability.lifecycle_state == lifecycle_state)
        if slug is not None:
            stmt = stmt.where(Capability.slug == slug)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(Capability.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, capability: Capability) -> Capability:
        self._session.add(capability)
        await flush_or_raise(self._session)
        return capability

    async def save(self, capability: Capability) -> Capability:
        await flush_or_raise(self._session)
        return capability

    async def add_realization(
        self, realization: CapabilityRealization
    ) -> CapabilityRealization:
        self._session.add(realization)
        await flush_or_raise(self._session)
        return realization

    async def list_realizations(
        self, capability_version_id: uuid.UUID
    ) -> list[CapabilityRealization]:
        result = await self._session.scalars(
            sa.select(CapabilityRealization).where(
                CapabilityRealization.capability_id == capability_version_id
            )
        )
        return list(result)
```

```python
# src/loom/api/catalog/capability/service.py
import datetime
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError, IllegalTransitionError
from loom.api.catalog.lifecycle import is_legal_transition
from loom.model.capability import Capability, CapabilityRealization
from loom.model.enums import LifecycleState

from .repository import CapabilityRepository
from .schemas import CapabilityCreateRequest, CapabilityRealizationCreateRequest


class CapabilityService:
    """Use-cases for the Capability aggregate."""

    def __init__(self, repository: CapabilityRepository) -> None:
        self._repository = repository

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: CapabilityCreateRequest,
    ) -> Capability:
        capability = Capability(
            tenant_id=tenant_id,
            owner_id=data.owner_id or created_by_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            target_metrics=data.target_metrics,
        )
        return await self._repository.add(capability)

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> Capability:
        capability = await self._repository.get_current(tenant_id, entity_id)
        if capability is None:
            raise EntityNotFoundError(f'Capability {entity_id} not found')
        return capability

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Capability:
        capability = await self._repository.get_version(tenant_id, entity_id, version)
        if capability is None:
            detail = f'Capability {entity_id} version {version} not found'
            raise EntityNotFoundError(detail)
        return capability

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[Capability]:
        versions = await self._repository.list_versions(tenant_id, entity_id)
        if not versions:
            raise EntityNotFoundError(f'Capability {entity_id} not found')
        return versions

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Capability], int]:
        return await self._repository.list_current(
            tenant_id,
            lifecycle_state=lifecycle_state,
            slug=slug,
            limit=limit,
            offset=offset,
        )

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: CapabilityCreateRequest,
    ) -> Capability:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = Capability(
            entity_id=entity_id,
            version=current.version + 1,
            is_current=True,
            tenant_id=tenant_id,
            owner_id=data.owner_id or current.owner_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            target_metrics=data.target_metrics,
        )
        return await self._repository.add(new_version)

    async def transition(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        to_state: LifecycleState,
        actor_id: uuid.UUID,
    ) -> Capability:
        current = await self.get_current(tenant_id, entity_id)
        if current.version != version:
            detail = f'Version {version} is not the current version of {entity_id}'
            raise IllegalTransitionError(detail)
        if not is_legal_transition(current.lifecycle_state, to_state):
            detail = f'{current.lifecycle_state} -> {to_state} is not a legal transition'
            raise IllegalTransitionError(detail)
        current.lifecycle_state = to_state
        if to_state == LifecycleState.APPROVED:
            current.approved_by_id = actor_id
            current.approved_at = datetime.datetime.now(datetime.UTC)
        return await self._repository.save(current)

    async def add_realization(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: CapabilityRealizationCreateRequest,
    ) -> CapabilityRealization:
        capability = await self.get_version(tenant_id, entity_id, version)
        realization = CapabilityRealization(
            capability_id=capability.id,
            realizing_entity_type=data.realizing_entity_type,
            realizing_agent_id=data.realizing_agent_id,
            realizing_skill_id=data.realizing_skill_id,
            realizing_tool_id=data.realizing_tool_id,
            contribution_weight=data.contribution_weight,
        )
        return await self._repository.add_realization(realization)

    async def list_realizations(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[CapabilityRealization]:
        capability = await self.get_version(tenant_id, entity_id, version)
        return await self._repository.list_realizations(capability.id)
```

```python
# src/loom/api/catalog/capability/router.py
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_current_principal, get_session, require_scopes
from loom.api.catalog.lifecycle import TransitionRequest
from loom.api.catalog.pagination import Page, PaginationParams
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.model.enums import LifecycleState
from loom.model.schemas.capability import CapabilityRead, CapabilityRealizationRead

from .repository import CapabilityRepository
from .schemas import CapabilityCreateRequest, CapabilityRealizationCreateRequest
from .service import CapabilityService

router = APIRouter(prefix='/capabilities', tags=['capabilities'])


def _service(session: AsyncSession = Depends(get_session)) -> CapabilityService:
    return CapabilityService(CapabilityRepository(session))


@router.post('', response_model=CapabilityRead, status_code=201)
async def create_capability(
    body: CapabilityCreateRequest,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:write')),
):
    return await service.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[CapabilityRead])
async def list_capabilities(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    slug: str | None = None,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:read')),
):
    items, total = await service.list_current(
        principal.tenant_id,
        lifecycle_state=lifecycle_state,
        slug=slug,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(items=items, total=total, limit=pagination.limit, offset=pagination.offset)


@router.get('/{entity_id}', response_model=CapabilityRead)
async def get_capability(
    entity_id: uuid.UUID,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:read')),
):
    return await service.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[CapabilityRead])
async def list_capability_versions(
    entity_id: uuid.UUID,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:read')),
):
    return await service.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=CapabilityRead)
async def get_capability_version(
    entity_id: uuid.UUID,
    version: int,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:read')),
):
    return await service.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=CapabilityRead, status_code=201)
async def create_capability_version(
    entity_id: uuid.UUID,
    body: CapabilityCreateRequest,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:write')),
):
    return await service.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post('/{entity_id}/versions/{version}/transitions', response_model=CapabilityRead)
async def transition_capability(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:transition')),
):
    return await service.transition(
        tenant_id=principal.tenant_id,
        entity_id=entity_id,
        version=version,
        to_state=body.to_state,
        actor_id=principal.principal_id,
    )


@router.post(
    '/{entity_id}/versions/{version}/realizations',
    response_model=CapabilityRealizationRead,
    status_code=201,
)
async def add_capability_realization(
    entity_id: uuid.UUID,
    version: int,
    body: CapabilityRealizationCreateRequest,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:write')),
):
    return await service.add_realization(
        tenant_id=principal.tenant_id, entity_id=entity_id, version=version, data=body
    )


@router.get(
    '/{entity_id}/versions/{version}/realizations',
    response_model=list[CapabilityRealizationRead],
)
async def list_capability_realizations(
    entity_id: uuid.UUID,
    version: int,
    service: CapabilityService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:capability:read')),
):
    return await service.list_realizations(principal.tenant_id, entity_id, version)
```

Note: the router is not yet mounted into `main.py`'s `app` — that happens in
Task 10, once every aggregate router exists. For this task's tests to reach
`/capabilities` at all, temporarily include the router directly inside the
`api_client` fixture's app-building step is NOT required — instead, this
task must ALSO make one small addition: mount `capability.router` into
`create_app` in `main.py` right now (it will be joined by the other 8
routers in Task 10). Add to `src/loom/api/catalog/main.py`, inside
`create_app`, after `register_exception_handlers(app)`:

```python
    from .capability.router import router as capability_router

    app.include_router(capability_router, prefix='/api/v1')
```

(A local import here, not a top-level one, is deliberate: `main.py` must not
import every aggregate submodule at module-import time before those
submodules exist — Task 10 replaces this with the full set of top-level
imports once all aggregates exist. Keep it local for now.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/api/catalog/test_capability.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/api/catalog/capability src/loom/api/catalog/main.py tests/api/catalog
git commit -m "Add Capability module: CRUD, versioning, transitions, realizations"
```

---

## Task 4: Agent module

Same Router → Service → Repository shape as Task 3's Capability module, with
no sub-resources (Agent has none). Follow the identical `add`/`save` split
and `flush_or_raise` usage.

**Files:**
- Create: `src/loom/api/catalog/agent/__init__.py`
- Create: `src/loom/api/catalog/agent/schemas.py`
- Create: `src/loom/api/catalog/agent/repository.py`
- Create: `src/loom/api/catalog/agent/service.py`
- Create: `src/loom/api/catalog/agent/router.py`
- Modify: `src/loom/api/catalog/main.py`
- Test: `tests/api/catalog/test_agent.py`

**Interfaces:**
- Consumes: same Task 2 shared infra as Task 3.
- Produces: `router` (`APIRouter`, mounted below), `AgentService`, `AgentRepository`.

- [ ] **Step 1: Write the failing tests**

```python
# src/loom/api/catalog/agent/__init__.py
```

```python
# tests/api/catalog/test_agent.py
import pytest


@pytest.mark.asyncio
async def test_agent_full_lifecycle(api_client):
    create_resp = await api_client.post(
        '/api/v1/agents',
        json={
            'slug': 'triage-agent',
            'name': 'Triage Agent',
            'layer': 'business_ops',
            'llm_config': {'provider': 'anthropic'},
            'prompt': 'You triage tickets.',
            'memory_scope': 'session',
        },
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    entity_id = created['entity_id']
    assert created['version'] == 1
    assert created['permission_boundary'] == {}

    get_resp = await api_client.get(f'/api/v1/agents/{entity_id}')
    assert get_resp.status_code == 200

    list_resp = await api_client.get('/api/v1/agents')
    assert list_resp.json()['total'] == 1

    version_resp = await api_client.post(
        f'/api/v1/agents/{entity_id}/versions',
        json={
            'slug': 'triage-agent',
            'name': 'Triage Agent v2',
            'layer': 'business_ops',
            'llm_config': {'provider': 'anthropic'},
            'prompt': 'You triage tickets, v2.',
            'memory_scope': 'session',
        },
    )
    assert version_resp.status_code == 201
    assert version_resp.json()['version'] == 2

    transition_resp = await api_client.post(
        f'/api/v1/agents/{entity_id}/versions/2/transitions', json={'to_state': 'in_review'}
    )
    assert transition_resp.status_code == 200
    assert transition_resp.json()['lifecycle_state'] == 'in_review'


@pytest.mark.asyncio
async def test_agent_not_found_returns_404(api_client):
    resp = await api_client.get('/api/v1/agents/00000000-0000-0000-0000-000000000000')
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/api/catalog/test_agent.py -v`
Expected: FAIL — `404 Not Found` for `/agents`

- [ ] **Step 3: Implement**

```python
# src/loom/api/catalog/agent/schemas.py
import uuid

from pydantic import BaseModel

from loom.model.enums import Layer, MemoryScope


class AgentCreateRequest(BaseModel):
    slug: str
    name: str
    description: str | None = None
    layer: Layer
    llm_config: dict
    prompt: str
    memory_scope: MemoryScope
    permission_boundary: dict = {}
    owner_id: uuid.UUID | None = None
```

```python
# src/loom/api/catalog/agent/repository.py
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.model.agent import Agent
from loom.model.enums import LifecycleState


class AgentRepository:
    """Async persistence access for Agent, scoped by tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> Agent | None:
        return await self._session.scalar(
            sa.select(Agent).where(
                Agent.tenant_id == tenant_id,
                Agent.entity_id == entity_id,
                Agent.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Agent | None:
        return await self._session.scalar(
            sa.select(Agent).where(
                Agent.tenant_id == tenant_id,
                Agent.entity_id == entity_id,
                Agent.version == version,
            )
        )

    async def list_versions(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> list[Agent]:
        result = await self._session.scalars(
            sa.select(Agent)
            .where(Agent.tenant_id == tenant_id, Agent.entity_id == entity_id)
            .order_by(Agent.version)
        )
        return list(result)

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Agent], int]:
        stmt = sa.select(Agent).where(
            Agent.tenant_id == tenant_id, Agent.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(Agent.lifecycle_state == lifecycle_state)
        if slug is not None:
            stmt = stmt.where(Agent.slug == slug)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(Agent.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, agent: Agent) -> Agent:
        self._session.add(agent)
        await flush_or_raise(self._session)
        return agent

    async def save(self, agent: Agent) -> Agent:
        await flush_or_raise(self._session)
        return agent
```

```python
# src/loom/api/catalog/agent/service.py
import datetime
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError, IllegalTransitionError
from loom.api.catalog.lifecycle import is_legal_transition
from loom.model.agent import Agent
from loom.model.enums import LifecycleState

from .repository import AgentRepository
from .schemas import AgentCreateRequest


class AgentService:
    """Use-cases for the Agent aggregate."""

    def __init__(self, repository: AgentRepository) -> None:
        self._repository = repository

    async def create(
        self, *, tenant_id: uuid.UUID, created_by_id: uuid.UUID, data: AgentCreateRequest
    ) -> Agent:
        agent = Agent(
            tenant_id=tenant_id,
            owner_id=data.owner_id or created_by_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            layer=data.layer,
            llm_config=data.llm_config,
            prompt=data.prompt,
            memory_scope=data.memory_scope,
            permission_boundary=data.permission_boundary,
        )
        return await self._repository.add(agent)

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> Agent:
        agent = await self._repository.get_current(tenant_id, entity_id)
        if agent is None:
            raise EntityNotFoundError(f'Agent {entity_id} not found')
        return agent

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Agent:
        agent = await self._repository.get_version(tenant_id, entity_id, version)
        if agent is None:
            raise EntityNotFoundError(f'Agent {entity_id} version {version} not found')
        return agent

    async def list_versions(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> list[Agent]:
        versions = await self._repository.list_versions(tenant_id, entity_id)
        if not versions:
            raise EntityNotFoundError(f'Agent {entity_id} not found')
        return versions

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Agent], int]:
        return await self._repository.list_current(
            tenant_id,
            lifecycle_state=lifecycle_state,
            slug=slug,
            limit=limit,
            offset=offset,
        )

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: AgentCreateRequest,
    ) -> Agent:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = Agent(
            entity_id=entity_id,
            version=current.version + 1,
            is_current=True,
            tenant_id=tenant_id,
            owner_id=data.owner_id or current.owner_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            layer=data.layer,
            llm_config=data.llm_config,
            prompt=data.prompt,
            memory_scope=data.memory_scope,
            permission_boundary=data.permission_boundary,
        )
        return await self._repository.add(new_version)

    async def transition(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        to_state: LifecycleState,
        actor_id: uuid.UUID,
    ) -> Agent:
        current = await self.get_current(tenant_id, entity_id)
        if current.version != version:
            detail = f'Version {version} is not the current version of {entity_id}'
            raise IllegalTransitionError(detail)
        if not is_legal_transition(current.lifecycle_state, to_state):
            detail = f'{current.lifecycle_state} -> {to_state} is not a legal transition'
            raise IllegalTransitionError(detail)
        current.lifecycle_state = to_state
        if to_state == LifecycleState.APPROVED:
            current.approved_by_id = actor_id
            current.approved_at = datetime.datetime.now(datetime.UTC)
        return await self._repository.save(current)
```

```python
# src/loom/api/catalog/agent/router.py
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_current_principal, get_session, require_scopes
from loom.api.catalog.lifecycle import TransitionRequest
from loom.api.catalog.pagination import Page, PaginationParams
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.model.enums import LifecycleState
from loom.model.schemas.agent import AgentRead

from .repository import AgentRepository
from .schemas import AgentCreateRequest
from .service import AgentService

router = APIRouter(prefix='/agents', tags=['agents'])


def _service(session: AsyncSession = Depends(get_session)) -> AgentService:
    return AgentService(AgentRepository(session))


@router.post('', response_model=AgentRead, status_code=201)
async def create_agent(
    body: AgentCreateRequest,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:write')),
):
    return await service.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[AgentRead])
async def list_agents(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    slug: str | None = None,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:read')),
):
    items, total = await service.list_current(
        principal.tenant_id,
        lifecycle_state=lifecycle_state,
        slug=slug,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(items=items, total=total, limit=pagination.limit, offset=pagination.offset)


@router.get('/{entity_id}', response_model=AgentRead)
async def get_agent(
    entity_id: uuid.UUID,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:read')),
):
    return await service.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[AgentRead])
async def list_agent_versions(
    entity_id: uuid.UUID,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:read')),
):
    return await service.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=AgentRead)
async def get_agent_version(
    entity_id: uuid.UUID,
    version: int,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:read')),
):
    return await service.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=AgentRead, status_code=201)
async def create_agent_version(
    entity_id: uuid.UUID,
    body: AgentCreateRequest,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:write')),
):
    return await service.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post('/{entity_id}/versions/{version}/transitions', response_model=AgentRead)
async def transition_agent(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    service: AgentService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:agent:transition')),
):
    return await service.transition(
        tenant_id=principal.tenant_id,
        entity_id=entity_id,
        version=version,
        to_state=body.to_state,
        actor_id=principal.principal_id,
    )
```

Modify `src/loom/api/catalog/main.py` — inside `create_app`, alongside the
Task 3 local import/include_router, add (still local imports for now,
Task 10 converts to top-level):

```python
    from .agent.router import router as agent_router

    app.include_router(agent_router, prefix='/api/v1')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/api/catalog/test_agent.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/api/catalog/agent src/loom/api/catalog/main.py tests/api/catalog/test_agent.py
git commit -m "Add Agent module: CRUD, versioning, transitions"
```

---

## Task 5: Skill module (+ graph nodes/edges)

Same shape as Capability (Task 3), plus two version-scoped sub-resources:
`SkillGraphNode` and `SkillGraphEdge`. The `kind=atomic ⇔ atomic_content
NOT NULL` invariant is already enforced by a DB CHECK constraint from the
prior plan — do not duplicate that validation in the service; let an
invalid combination surface as a 422 via `flush_or_raise`'s translation.

**Files:**
- Create: `src/loom/api/catalog/skill/__init__.py`
- Create: `src/loom/api/catalog/skill/schemas.py`
- Create: `src/loom/api/catalog/skill/repository.py`
- Create: `src/loom/api/catalog/skill/service.py`
- Create: `src/loom/api/catalog/skill/router.py`
- Modify: `src/loom/api/catalog/main.py`
- Test: `tests/api/catalog/test_skill.py`

**Interfaces:**
- Consumes: same Task 2 shared infra as Task 3.
- Produces: `router`, `SkillService`, `SkillRepository`.

- [ ] **Step 1: Write the failing tests**

```python
# src/loom/api/catalog/skill/__init__.py
```

```python
# tests/api/catalog/test_skill.py
import pytest


@pytest.mark.asyncio
async def test_atomic_skill_requires_content_returns_422(api_client):
    resp = await api_client.post(
        '/api/v1/skills',
        json={
            'slug': 'draft-reply',
            'name': 'Draft Reply',
            'layer': 'business_ops',
            'kind': 'atomic',
            'atomic_content': None,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_composite_skill_graph_lifecycle(api_client):
    create_resp = await api_client.post(
        '/api/v1/skills',
        json={
            'slug': 'triage-flow',
            'name': 'Triage Flow',
            'layer': 'business_ops',
            'kind': 'composite',
            'is_entry_point': True,
        },
    )
    assert create_resp.status_code == 201
    entity_id = create_resp.json()['entity_id']

    agent_resp = await api_client.post(
        '/api/v1/agents',
        json={
            'slug': 'triage-agent',
            'name': 'Triage Agent',
            'layer': 'business_ops',
            'llm_config': {},
            'prompt': 'p',
            'memory_scope': 'none',
        },
    )
    agent_version_id = agent_resp.json()['id']

    node_resp = await api_client.post(
        '/api/v1/skills/' + entity_id + '/versions/1/nodes',
        json={'node_key': 'start', 'node_type': 'agent', 'agent_id': agent_version_id},
    )
    assert node_resp.status_code == 201
    node_id = node_resp.json()['id']

    nodes_resp = await api_client.get('/api/v1/skills/' + entity_id + '/versions/1/nodes')
    assert len(nodes_resp.json()) == 1

    edge_resp = await api_client.post(
        '/api/v1/skills/' + entity_id + '/versions/1/edges',
        json={'from_node_id': node_id, 'to_node_id': node_id},
    )
    assert edge_resp.status_code == 201

    edges_resp = await api_client.get('/api/v1/skills/' + entity_id + '/versions/1/edges')
    assert len(edges_resp.json()) == 1

    transition_resp = await api_client.post(
        '/api/v1/skills/' + entity_id + '/versions/1/transitions', json={'to_state': 'in_review'}
    )
    assert transition_resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/api/catalog/test_skill.py -v`
Expected: FAIL — `404 Not Found` for `/skills`

- [ ] **Step 3: Implement**

```python
# src/loom/api/catalog/skill/schemas.py
import uuid

from pydantic import BaseModel

from loom.model.enums import GraphNodeType, Layer, SkillKind


class SkillCreateRequest(BaseModel):
    slug: str
    name: str
    description: str | None = None
    layer: Layer
    kind: SkillKind
    is_entry_point: bool = False
    atomic_content: dict | None = None
    owner_id: uuid.UUID | None = None


class SkillGraphNodeCreateRequest(BaseModel):
    node_key: str
    node_type: GraphNodeType
    agent_id: uuid.UUID | None = None
    skill_ref_id: uuid.UUID | None = None
    tool_id: uuid.UUID | None = None
    position: dict | None = None


class SkillGraphEdgeCreateRequest(BaseModel):
    from_node_id: uuid.UUID
    to_node_id: uuid.UUID
```

```python
# src/loom/api/catalog/skill/repository.py
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.model.enums import LifecycleState
from loom.model.skill import Skill, SkillGraphEdge, SkillGraphNode


class SkillRepository:
    """Async persistence access for Skill, scoped by tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> Skill | None:
        return await self._session.scalar(
            sa.select(Skill).where(
                Skill.tenant_id == tenant_id,
                Skill.entity_id == entity_id,
                Skill.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Skill | None:
        return await self._session.scalar(
            sa.select(Skill).where(
                Skill.tenant_id == tenant_id,
                Skill.entity_id == entity_id,
                Skill.version == version,
            )
        )

    async def list_versions(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> list[Skill]:
        result = await self._session.scalars(
            sa.select(Skill)
            .where(Skill.tenant_id == tenant_id, Skill.entity_id == entity_id)
            .order_by(Skill.version)
        )
        return list(result)

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Skill], int]:
        stmt = sa.select(Skill).where(
            Skill.tenant_id == tenant_id, Skill.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(Skill.lifecycle_state == lifecycle_state)
        if slug is not None:
            stmt = stmt.where(Skill.slug == slug)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(Skill.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, skill: Skill) -> Skill:
        self._session.add(skill)
        await flush_or_raise(self._session)
        return skill

    async def save(self, skill: Skill) -> Skill:
        await flush_or_raise(self._session)
        return skill

    async def add_node(self, node: SkillGraphNode) -> SkillGraphNode:
        self._session.add(node)
        await flush_or_raise(self._session)
        return node

    async def list_nodes(self, skill_version_id: uuid.UUID) -> list[SkillGraphNode]:
        result = await self._session.scalars(
            sa.select(SkillGraphNode).where(SkillGraphNode.skill_id == skill_version_id)
        )
        return list(result)

    async def add_edge(self, edge: SkillGraphEdge) -> SkillGraphEdge:
        self._session.add(edge)
        await flush_or_raise(self._session)
        return edge

    async def list_edges(self, skill_version_id: uuid.UUID) -> list[SkillGraphEdge]:
        result = await self._session.scalars(
            sa.select(SkillGraphEdge).where(SkillGraphEdge.skill_id == skill_version_id)
        )
        return list(result)
```

```python
# src/loom/api/catalog/skill/service.py
import datetime
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError, IllegalTransitionError
from loom.api.catalog.lifecycle import is_legal_transition
from loom.model.enums import LifecycleState
from loom.model.skill import Skill, SkillGraphEdge, SkillGraphNode

from .repository import SkillRepository
from .schemas import SkillCreateRequest, SkillGraphEdgeCreateRequest, SkillGraphNodeCreateRequest


class SkillService:
    """Use-cases for the Skill aggregate."""

    def __init__(self, repository: SkillRepository) -> None:
        self._repository = repository

    async def create(
        self, *, tenant_id: uuid.UUID, created_by_id: uuid.UUID, data: SkillCreateRequest
    ) -> Skill:
        skill = Skill(
            tenant_id=tenant_id,
            owner_id=data.owner_id or created_by_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            layer=data.layer,
            kind=data.kind,
            is_entry_point=data.is_entry_point,
            atomic_content=data.atomic_content,
        )
        return await self._repository.add(skill)

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> Skill:
        skill = await self._repository.get_current(tenant_id, entity_id)
        if skill is None:
            raise EntityNotFoundError(f'Skill {entity_id} not found')
        return skill

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Skill:
        skill = await self._repository.get_version(tenant_id, entity_id, version)
        if skill is None:
            raise EntityNotFoundError(f'Skill {entity_id} version {version} not found')
        return skill

    async def list_versions(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> list[Skill]:
        versions = await self._repository.list_versions(tenant_id, entity_id)
        if not versions:
            raise EntityNotFoundError(f'Skill {entity_id} not found')
        return versions

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Skill], int]:
        return await self._repository.list_current(
            tenant_id,
            lifecycle_state=lifecycle_state,
            slug=slug,
            limit=limit,
            offset=offset,
        )

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: SkillCreateRequest,
    ) -> Skill:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = Skill(
            entity_id=entity_id,
            version=current.version + 1,
            is_current=True,
            tenant_id=tenant_id,
            owner_id=data.owner_id or current.owner_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            layer=data.layer,
            kind=data.kind,
            is_entry_point=data.is_entry_point,
            atomic_content=data.atomic_content,
        )
        return await self._repository.add(new_version)

    async def transition(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        to_state: LifecycleState,
        actor_id: uuid.UUID,
    ) -> Skill:
        current = await self.get_current(tenant_id, entity_id)
        if current.version != version:
            detail = f'Version {version} is not the current version of {entity_id}'
            raise IllegalTransitionError(detail)
        if not is_legal_transition(current.lifecycle_state, to_state):
            detail = f'{current.lifecycle_state} -> {to_state} is not a legal transition'
            raise IllegalTransitionError(detail)
        current.lifecycle_state = to_state
        if to_state == LifecycleState.APPROVED:
            current.approved_by_id = actor_id
            current.approved_at = datetime.datetime.now(datetime.UTC)
        return await self._repository.save(current)

    async def add_node(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: SkillGraphNodeCreateRequest,
    ) -> SkillGraphNode:
        skill = await self.get_version(tenant_id, entity_id, version)
        node = SkillGraphNode(
            skill_id=skill.id,
            node_key=data.node_key,
            node_type=data.node_type,
            agent_id=data.agent_id,
            skill_ref_id=data.skill_ref_id,
            tool_id=data.tool_id,
            position=data.position,
        )
        return await self._repository.add_node(node)

    async def list_nodes(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[SkillGraphNode]:
        skill = await self.get_version(tenant_id, entity_id, version)
        return await self._repository.list_nodes(skill.id)

    async def add_edge(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: SkillGraphEdgeCreateRequest,
    ) -> SkillGraphEdge:
        skill = await self.get_version(tenant_id, entity_id, version)
        edge = SkillGraphEdge(
            skill_id=skill.id, from_node_id=data.from_node_id, to_node_id=data.to_node_id
        )
        return await self._repository.add_edge(edge)

    async def list_edges(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[SkillGraphEdge]:
        skill = await self.get_version(tenant_id, entity_id, version)
        return await self._repository.list_edges(skill.id)
```

```python
# src/loom/api/catalog/skill/router.py
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_current_principal, get_session, require_scopes
from loom.api.catalog.lifecycle import TransitionRequest
from loom.api.catalog.pagination import Page, PaginationParams
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.model.enums import LifecycleState
from loom.model.schemas.skill import SkillGraphEdgeRead, SkillGraphNodeRead, SkillRead

from .repository import SkillRepository
from .schemas import SkillCreateRequest, SkillGraphEdgeCreateRequest, SkillGraphNodeCreateRequest
from .service import SkillService

router = APIRouter(prefix='/skills', tags=['skills'])


def _service(session: AsyncSession = Depends(get_session)) -> SkillService:
    return SkillService(SkillRepository(session))


@router.post('', response_model=SkillRead, status_code=201)
async def create_skill(
    body: SkillCreateRequest,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:write')),
):
    return await service.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[SkillRead])
async def list_skills(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    slug: str | None = None,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    items, total = await service.list_current(
        principal.tenant_id,
        lifecycle_state=lifecycle_state,
        slug=slug,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(items=items, total=total, limit=pagination.limit, offset=pagination.offset)


@router.get('/{entity_id}', response_model=SkillRead)
async def get_skill(
    entity_id: uuid.UUID,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await service.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[SkillRead])
async def list_skill_versions(
    entity_id: uuid.UUID,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await service.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=SkillRead)
async def get_skill_version(
    entity_id: uuid.UUID,
    version: int,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await service.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=SkillRead, status_code=201)
async def create_skill_version(
    entity_id: uuid.UUID,
    body: SkillCreateRequest,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:write')),
):
    return await service.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post('/{entity_id}/versions/{version}/transitions', response_model=SkillRead)
async def transition_skill(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:transition')),
):
    return await service.transition(
        tenant_id=principal.tenant_id,
        entity_id=entity_id,
        version=version,
        to_state=body.to_state,
        actor_id=principal.principal_id,
    )


@router.post(
    '/{entity_id}/versions/{version}/nodes', response_model=SkillGraphNodeRead, status_code=201
)
async def add_skill_node(
    entity_id: uuid.UUID,
    version: int,
    body: SkillGraphNodeCreateRequest,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:write')),
):
    return await service.add_node(
        tenant_id=principal.tenant_id, entity_id=entity_id, version=version, data=body
    )


@router.get('/{entity_id}/versions/{version}/nodes', response_model=list[SkillGraphNodeRead])
async def list_skill_nodes(
    entity_id: uuid.UUID,
    version: int,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await service.list_nodes(principal.tenant_id, entity_id, version)


@router.post(
    '/{entity_id}/versions/{version}/edges', response_model=SkillGraphEdgeRead, status_code=201
)
async def add_skill_edge(
    entity_id: uuid.UUID,
    version: int,
    body: SkillGraphEdgeCreateRequest,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:write')),
):
    return await service.add_edge(
        tenant_id=principal.tenant_id, entity_id=entity_id, version=version, data=body
    )


@router.get('/{entity_id}/versions/{version}/edges', response_model=list[SkillGraphEdgeRead])
async def list_skill_edges(
    entity_id: uuid.UUID,
    version: int,
    service: SkillService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:skill:read')),
):
    return await service.list_edges(principal.tenant_id, entity_id, version)
```

Modify `src/loom/api/catalog/main.py` — add alongside the existing local
imports/`include_router` calls inside `create_app`:

```python
    from .skill.router import router as skill_router

    app.include_router(skill_router, prefix='/api/v1')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/api/catalog/test_skill.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/api/catalog/skill src/loom/api/catalog/main.py tests/api/catalog/test_skill.py
git commit -m "Add Skill module: CRUD, versioning, transitions, graph nodes/edges"
```

---

## Task 6: Tool module (+ data bindings)

Same shape as Capability (Task 3), plus one version-scoped sub-resource:
`ToolDataBinding` (version-pinned — its `datasource_id`/`dataproduct_id`
FKs point at a specific `DataSource`/`DataProduct` **version row**, not the
floating `entity_id`; the client supplies those version-row UUIDs
directly).

**Files:**
- Create: `src/loom/api/catalog/tool/__init__.py`
- Create: `src/loom/api/catalog/tool/schemas.py`
- Create: `src/loom/api/catalog/tool/repository.py`
- Create: `src/loom/api/catalog/tool/service.py`
- Create: `src/loom/api/catalog/tool/router.py`
- Modify: `src/loom/api/catalog/main.py`
- Test: `tests/api/catalog/test_tool.py`

**Interfaces:**
- Consumes: same Task 2 shared infra as Task 3.
- Produces: `router`, `ToolService`, `ToolRepository`.

- [ ] **Step 1: Write the failing tests**

```python
# src/loom/api/catalog/tool/__init__.py
```

```python
# tests/api/catalog/test_tool.py
import pytest


@pytest.mark.asyncio
async def test_tool_full_lifecycle(api_client):
    create_resp = await api_client.post(
        '/api/v1/tools',
        json={
            'slug': 'send-email',
            'name': 'Send Email',
            'invocation_spec': {'method': 'POST', 'path': '/v1/email'},
        },
    )
    assert create_resp.status_code == 201
    entity_id = create_resp.json()['entity_id']

    get_resp = await api_client.get(f'/api/v1/tools/{entity_id}')
    assert get_resp.status_code == 200
    assert get_resp.json()['auth_binding_id'] is None

    version_resp = await api_client.post(
        f'/api/v1/tools/{entity_id}/versions',
        json={
            'slug': 'send-email',
            'name': 'Send Email v2',
            'invocation_spec': {'method': 'POST', 'path': '/v2/email'},
        },
    )
    assert version_resp.status_code == 201
    assert version_resp.json()['version'] == 2


@pytest.mark.asyncio
async def test_tool_data_binding_is_version_scoped(api_client, fake_principal, async_session):
    from loom.model.datasource import DataSource
    from loom.model.enums import DataSourceKind

    datasource = DataSource(
        tenant_id=fake_principal.tenant_id,
        owner_id=fake_principal.principal_id,
        created_by_id=fake_principal.principal_id,
        slug='orders-db',
        name='Orders DB',
        kind=DataSourceKind.DATABASE,
    )
    async_session.add(datasource)
    await async_session.commit()

    create_resp = await api_client.post(
        '/api/v1/tools', json={'slug': 'query-orders', 'name': 'Query Orders', 'invocation_spec': {}}
    )
    entity_id = create_resp.json()['entity_id']

    binding_resp = await api_client.post(
        f'/api/v1/tools/{entity_id}/versions/1/data-bindings',
        json={'datasource_id': str(datasource.id), 'access_mode': 'read'},
    )
    assert binding_resp.status_code == 201

    list_resp = await api_client.get(f'/api/v1/tools/{entity_id}/versions/1/data-bindings')
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/api/catalog/test_tool.py -v`
Expected: FAIL — `404 Not Found` for `/tools`

- [ ] **Step 3: Implement**

```python
# src/loom/api/catalog/tool/schemas.py
import uuid

from pydantic import BaseModel

from loom.model.enums import DataBindingAccessMode


class ToolCreateRequest(BaseModel):
    slug: str
    name: str
    description: str | None = None
    invocation_spec: dict
    auth_binding_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None


class ToolDataBindingCreateRequest(BaseModel):
    datasource_id: uuid.UUID | None = None
    dataproduct_id: uuid.UUID | None = None
    access_mode: DataBindingAccessMode
```

```python
# src/loom/api/catalog/tool/repository.py
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.model.enums import LifecycleState
from loom.model.tool import Tool, ToolDataBinding


class ToolRepository:
    """Async persistence access for Tool, scoped by tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> Tool | None:
        return await self._session.scalar(
            sa.select(Tool).where(
                Tool.tenant_id == tenant_id,
                Tool.entity_id == entity_id,
                Tool.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Tool | None:
        return await self._session.scalar(
            sa.select(Tool).where(
                Tool.tenant_id == tenant_id,
                Tool.entity_id == entity_id,
                Tool.version == version,
            )
        )

    async def list_versions(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> list[Tool]:
        result = await self._session.scalars(
            sa.select(Tool)
            .where(Tool.tenant_id == tenant_id, Tool.entity_id == entity_id)
            .order_by(Tool.version)
        )
        return list(result)

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Tool], int]:
        stmt = sa.select(Tool).where(
            Tool.tenant_id == tenant_id, Tool.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(Tool.lifecycle_state == lifecycle_state)
        if slug is not None:
            stmt = stmt.where(Tool.slug == slug)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(Tool.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, tool: Tool) -> Tool:
        self._session.add(tool)
        await flush_or_raise(self._session)
        return tool

    async def save(self, tool: Tool) -> Tool:
        await flush_or_raise(self._session)
        return tool

    async def add_data_binding(self, binding: ToolDataBinding) -> ToolDataBinding:
        self._session.add(binding)
        await flush_or_raise(self._session)
        return binding

    async def list_data_bindings(self, tool_version_id: uuid.UUID) -> list[ToolDataBinding]:
        result = await self._session.scalars(
            sa.select(ToolDataBinding).where(ToolDataBinding.tool_id == tool_version_id)
        )
        return list(result)
```

```python
# src/loom/api/catalog/tool/service.py
import datetime
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError, IllegalTransitionError
from loom.api.catalog.lifecycle import is_legal_transition
from loom.model.enums import LifecycleState
from loom.model.tool import Tool, ToolDataBinding

from .repository import ToolRepository
from .schemas import ToolCreateRequest, ToolDataBindingCreateRequest


class ToolService:
    """Use-cases for the Tool aggregate."""

    def __init__(self, repository: ToolRepository) -> None:
        self._repository = repository

    async def create(
        self, *, tenant_id: uuid.UUID, created_by_id: uuid.UUID, data: ToolCreateRequest
    ) -> Tool:
        tool = Tool(
            tenant_id=tenant_id,
            owner_id=data.owner_id or created_by_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            invocation_spec=data.invocation_spec,
            auth_binding_id=data.auth_binding_id,
        )
        return await self._repository.add(tool)

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> Tool:
        tool = await self._repository.get_current(tenant_id, entity_id)
        if tool is None:
            raise EntityNotFoundError(f'Tool {entity_id} not found')
        return tool

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> Tool:
        tool = await self._repository.get_version(tenant_id, entity_id, version)
        if tool is None:
            raise EntityNotFoundError(f'Tool {entity_id} version {version} not found')
        return tool

    async def list_versions(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> list[Tool]:
        versions = await self._repository.list_versions(tenant_id, entity_id)
        if not versions:
            raise EntityNotFoundError(f'Tool {entity_id} not found')
        return versions

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Tool], int]:
        return await self._repository.list_current(
            tenant_id,
            lifecycle_state=lifecycle_state,
            slug=slug,
            limit=limit,
            offset=offset,
        )

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: ToolCreateRequest,
    ) -> Tool:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = Tool(
            entity_id=entity_id,
            version=current.version + 1,
            is_current=True,
            tenant_id=tenant_id,
            owner_id=data.owner_id or current.owner_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            invocation_spec=data.invocation_spec,
            auth_binding_id=data.auth_binding_id,
        )
        return await self._repository.add(new_version)

    async def transition(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        to_state: LifecycleState,
        actor_id: uuid.UUID,
    ) -> Tool:
        current = await self.get_current(tenant_id, entity_id)
        if current.version != version:
            detail = f'Version {version} is not the current version of {entity_id}'
            raise IllegalTransitionError(detail)
        if not is_legal_transition(current.lifecycle_state, to_state):
            detail = f'{current.lifecycle_state} -> {to_state} is not a legal transition'
            raise IllegalTransitionError(detail)
        current.lifecycle_state = to_state
        if to_state == LifecycleState.APPROVED:
            current.approved_by_id = actor_id
            current.approved_at = datetime.datetime.now(datetime.UTC)
        return await self._repository.save(current)

    async def add_data_binding(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: ToolDataBindingCreateRequest,
    ) -> ToolDataBinding:
        tool = await self.get_version(tenant_id, entity_id, version)
        binding = ToolDataBinding(
            tool_id=tool.id,
            datasource_id=data.datasource_id,
            dataproduct_id=data.dataproduct_id,
            access_mode=data.access_mode,
        )
        return await self._repository.add_data_binding(binding)

    async def list_data_bindings(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[ToolDataBinding]:
        tool = await self.get_version(tenant_id, entity_id, version)
        return await self._repository.list_data_bindings(tool.id)
```

```python
# src/loom/api/catalog/tool/router.py
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_current_principal, get_session, require_scopes
from loom.api.catalog.lifecycle import TransitionRequest
from loom.api.catalog.pagination import Page, PaginationParams
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.model.enums import LifecycleState
from loom.model.schemas.tool import ToolDataBindingRead, ToolRead

from .repository import ToolRepository
from .schemas import ToolCreateRequest, ToolDataBindingCreateRequest
from .service import ToolService

router = APIRouter(prefix='/tools', tags=['tools'])


def _service(session: AsyncSession = Depends(get_session)) -> ToolService:
    return ToolService(ToolRepository(session))


@router.post('', response_model=ToolRead, status_code=201)
async def create_tool(
    body: ToolCreateRequest,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:write')),
):
    return await service.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[ToolRead])
async def list_tools(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    slug: str | None = None,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:read')),
):
    items, total = await service.list_current(
        principal.tenant_id,
        lifecycle_state=lifecycle_state,
        slug=slug,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(items=items, total=total, limit=pagination.limit, offset=pagination.offset)


@router.get('/{entity_id}', response_model=ToolRead)
async def get_tool(
    entity_id: uuid.UUID,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:read')),
):
    return await service.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[ToolRead])
async def list_tool_versions(
    entity_id: uuid.UUID,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:read')),
):
    return await service.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=ToolRead)
async def get_tool_version(
    entity_id: uuid.UUID,
    version: int,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:read')),
):
    return await service.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=ToolRead, status_code=201)
async def create_tool_version(
    entity_id: uuid.UUID,
    body: ToolCreateRequest,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:write')),
):
    return await service.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post('/{entity_id}/versions/{version}/transitions', response_model=ToolRead)
async def transition_tool(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:transition')),
):
    return await service.transition(
        tenant_id=principal.tenant_id,
        entity_id=entity_id,
        version=version,
        to_state=body.to_state,
        actor_id=principal.principal_id,
    )


@router.post(
    '/{entity_id}/versions/{version}/data-bindings',
    response_model=ToolDataBindingRead,
    status_code=201,
)
async def add_tool_data_binding(
    entity_id: uuid.UUID,
    version: int,
    body: ToolDataBindingCreateRequest,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:write')),
):
    return await service.add_data_binding(
        tenant_id=principal.tenant_id, entity_id=entity_id, version=version, data=body
    )


@router.get(
    '/{entity_id}/versions/{version}/data-bindings',
    response_model=list[ToolDataBindingRead],
)
async def list_tool_data_bindings(
    entity_id: uuid.UUID,
    version: int,
    service: ToolService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:tool:read')),
):
    return await service.list_data_bindings(principal.tenant_id, entity_id, version)
```

Modify `src/loom/api/catalog/main.py` — add alongside the existing local
imports/`include_router` calls inside `create_app`:

```python
    from .tool.router import router as tool_router

    app.include_router(tool_router, prefix='/api/v1')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/api/catalog/test_tool.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/api/catalog/tool src/loom/api/catalog/main.py tests/api/catalog/test_tool.py
git commit -m "Add Tool module: CRUD, versioning, transitions, data bindings"
```

---

## Task 7: DataSource module

Same shape as Agent (Task 4) — no sub-resources.

**Files:**
- Create: `src/loom/api/catalog/datasource/__init__.py`
- Create: `src/loom/api/catalog/datasource/schemas.py`
- Create: `src/loom/api/catalog/datasource/repository.py`
- Create: `src/loom/api/catalog/datasource/service.py`
- Create: `src/loom/api/catalog/datasource/router.py`
- Modify: `src/loom/api/catalog/main.py`
- Test: `tests/api/catalog/test_datasource.py`

**Interfaces:**
- Consumes: same Task 2 shared infra as Task 3.
- Produces: `router`, `DataSourceService`, `DataSourceRepository`.

- [ ] **Step 1: Write the failing tests**

```python
# src/loom/api/catalog/datasource/__init__.py
```

```python
# tests/api/catalog/test_datasource.py
import pytest


@pytest.mark.asyncio
async def test_datasource_full_lifecycle(api_client):
    create_resp = await api_client.post(
        '/api/v1/datasources', json={'slug': 'orders-db', 'name': 'Orders DB', 'kind': 'database'}
    )
    assert create_resp.status_code == 201
    entity_id = create_resp.json()['entity_id']

    get_resp = await api_client.get(f'/api/v1/datasources/{entity_id}')
    assert get_resp.status_code == 200
    assert get_resp.json()['kind'] == 'database'

    version_resp = await api_client.post(
        f'/api/v1/datasources/{entity_id}/versions',
        json={'slug': 'orders-db', 'name': 'Orders DB v2', 'kind': 'database'},
    )
    assert version_resp.status_code == 201
    assert version_resp.json()['version'] == 2

    transition_resp = await api_client.post(
        f'/api/v1/datasources/{entity_id}/versions/2/transitions', json={'to_state': 'in_review'}
    )
    assert transition_resp.status_code == 200


@pytest.mark.asyncio
async def test_datasource_not_found_returns_404(api_client):
    resp = await api_client.get('/api/v1/datasources/00000000-0000-0000-0000-000000000000')
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/api/catalog/test_datasource.py -v`
Expected: FAIL — `404 Not Found` for `/datasources`

- [ ] **Step 3: Implement**

```python
# src/loom/api/catalog/datasource/schemas.py
import uuid

from pydantic import BaseModel

from loom.model.enums import DataSourceKind


class DataSourceCreateRequest(BaseModel):
    slug: str
    name: str
    description: str | None = None
    kind: DataSourceKind
    connection_binding_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
```

```python
# src/loom/api/catalog/datasource/repository.py
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.model.datasource import DataSource
from loom.model.enums import LifecycleState


class DataSourceRepository:
    """Async persistence access for DataSource, scoped by tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> DataSource | None:
        return await self._session.scalar(
            sa.select(DataSource).where(
                DataSource.tenant_id == tenant_id,
                DataSource.entity_id == entity_id,
                DataSource.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> DataSource | None:
        return await self._session.scalar(
            sa.select(DataSource).where(
                DataSource.tenant_id == tenant_id,
                DataSource.entity_id == entity_id,
                DataSource.version == version,
            )
        )

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[DataSource]:
        result = await self._session.scalars(
            sa.select(DataSource)
            .where(DataSource.tenant_id == tenant_id, DataSource.entity_id == entity_id)
            .order_by(DataSource.version)
        )
        return list(result)

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DataSource], int]:
        stmt = sa.select(DataSource).where(
            DataSource.tenant_id == tenant_id, DataSource.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(DataSource.lifecycle_state == lifecycle_state)
        if slug is not None:
            stmt = stmt.where(DataSource.slug == slug)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(DataSource.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, datasource: DataSource) -> DataSource:
        self._session.add(datasource)
        await flush_or_raise(self._session)
        return datasource

    async def save(self, datasource: DataSource) -> DataSource:
        await flush_or_raise(self._session)
        return datasource
```

```python
# src/loom/api/catalog/datasource/service.py
import datetime
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError, IllegalTransitionError
from loom.api.catalog.lifecycle import is_legal_transition
from loom.model.datasource import DataSource
from loom.model.enums import LifecycleState

from .repository import DataSourceRepository
from .schemas import DataSourceCreateRequest


class DataSourceService:
    """Use-cases for the DataSource aggregate."""

    def __init__(self, repository: DataSourceRepository) -> None:
        self._repository = repository

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: DataSourceCreateRequest,
    ) -> DataSource:
        datasource = DataSource(
            tenant_id=tenant_id,
            owner_id=data.owner_id or created_by_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            kind=data.kind,
            connection_binding_id=data.connection_binding_id,
        )
        return await self._repository.add(datasource)

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> DataSource:
        datasource = await self._repository.get_current(tenant_id, entity_id)
        if datasource is None:
            raise EntityNotFoundError(f'DataSource {entity_id} not found')
        return datasource

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> DataSource:
        datasource = await self._repository.get_version(tenant_id, entity_id, version)
        if datasource is None:
            detail = f'DataSource {entity_id} version {version} not found'
            raise EntityNotFoundError(detail)
        return datasource

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[DataSource]:
        versions = await self._repository.list_versions(tenant_id, entity_id)
        if not versions:
            raise EntityNotFoundError(f'DataSource {entity_id} not found')
        return versions

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DataSource], int]:
        return await self._repository.list_current(
            tenant_id,
            lifecycle_state=lifecycle_state,
            slug=slug,
            limit=limit,
            offset=offset,
        )

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: DataSourceCreateRequest,
    ) -> DataSource:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = DataSource(
            entity_id=entity_id,
            version=current.version + 1,
            is_current=True,
            tenant_id=tenant_id,
            owner_id=data.owner_id or current.owner_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            kind=data.kind,
            connection_binding_id=data.connection_binding_id,
        )
        return await self._repository.add(new_version)

    async def transition(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        to_state: LifecycleState,
        actor_id: uuid.UUID,
    ) -> DataSource:
        current = await self.get_current(tenant_id, entity_id)
        if current.version != version:
            detail = f'Version {version} is not the current version of {entity_id}'
            raise IllegalTransitionError(detail)
        if not is_legal_transition(current.lifecycle_state, to_state):
            detail = f'{current.lifecycle_state} -> {to_state} is not a legal transition'
            raise IllegalTransitionError(detail)
        current.lifecycle_state = to_state
        if to_state == LifecycleState.APPROVED:
            current.approved_by_id = actor_id
            current.approved_at = datetime.datetime.now(datetime.UTC)
        return await self._repository.save(current)
```

```python
# src/loom/api/catalog/datasource/router.py
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_current_principal, get_session, require_scopes
from loom.api.catalog.lifecycle import TransitionRequest
from loom.api.catalog.pagination import Page, PaginationParams
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.model.enums import LifecycleState
from loom.model.schemas.datasource import DataSourceRead

from .repository import DataSourceRepository
from .schemas import DataSourceCreateRequest
from .service import DataSourceService

router = APIRouter(prefix='/datasources', tags=['datasources'])


def _service(session: AsyncSession = Depends(get_session)) -> DataSourceService:
    return DataSourceService(DataSourceRepository(session))


@router.post('', response_model=DataSourceRead, status_code=201)
async def create_datasource(
    body: DataSourceCreateRequest,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:write')),
):
    return await service.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[DataSourceRead])
async def list_datasources(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    slug: str | None = None,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:read')),
):
    items, total = await service.list_current(
        principal.tenant_id,
        lifecycle_state=lifecycle_state,
        slug=slug,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(items=items, total=total, limit=pagination.limit, offset=pagination.offset)


@router.get('/{entity_id}', response_model=DataSourceRead)
async def get_datasource(
    entity_id: uuid.UUID,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:read')),
):
    return await service.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[DataSourceRead])
async def list_datasource_versions(
    entity_id: uuid.UUID,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:read')),
):
    return await service.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=DataSourceRead)
async def get_datasource_version(
    entity_id: uuid.UUID,
    version: int,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:read')),
):
    return await service.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=DataSourceRead, status_code=201)
async def create_datasource_version(
    entity_id: uuid.UUID,
    body: DataSourceCreateRequest,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:write')),
):
    return await service.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post('/{entity_id}/versions/{version}/transitions', response_model=DataSourceRead)
async def transition_datasource(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    service: DataSourceService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:datasource:transition')),
):
    return await service.transition(
        tenant_id=principal.tenant_id,
        entity_id=entity_id,
        version=version,
        to_state=body.to_state,
        actor_id=principal.principal_id,
    )
```

Modify `src/loom/api/catalog/main.py` — add alongside the existing local
imports/`include_router` calls inside `create_app`:

```python
    from .datasource.router import router as datasource_router

    app.include_router(datasource_router, prefix='/api/v1')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/api/catalog/test_datasource.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/api/catalog/datasource src/loom/api/catalog/main.py tests/api/catalog/test_datasource.py
git commit -m "Add DataSource module: CRUD, versioning, transitions"
```

---

## Task 8: DataProduct module (+ lineage)

Same shape as Capability (Task 3), with one version-scoped sub-resource:
`DataProductLineage`.

**Files:**
- Create: `src/loom/api/catalog/dataproduct/__init__.py`
- Create: `src/loom/api/catalog/dataproduct/schemas.py`
- Create: `src/loom/api/catalog/dataproduct/repository.py`
- Create: `src/loom/api/catalog/dataproduct/service.py`
- Create: `src/loom/api/catalog/dataproduct/router.py`
- Modify: `src/loom/api/catalog/main.py`
- Test: `tests/api/catalog/test_dataproduct.py`

**Interfaces:**
- Consumes: same Task 2 shared infra as Task 3.
- Produces: `router`, `DataProductService`, `DataProductRepository`.

- [ ] **Step 1: Write the failing tests**

```python
# src/loom/api/catalog/dataproduct/__init__.py
```

```python
# tests/api/catalog/test_dataproduct.py
import pytest


@pytest.mark.asyncio
async def test_dataproduct_full_lifecycle(api_client):
    create_resp = await api_client.post(
        '/api/v1/dataproducts',
        json={'slug': 'orders-curated', 'name': 'Curated Orders', 'contract': {}},
    )
    assert create_resp.status_code == 201
    entity_id = create_resp.json()['entity_id']

    get_resp = await api_client.get(f'/api/v1/dataproducts/{entity_id}')
    assert get_resp.status_code == 200

    version_resp = await api_client.post(
        f'/api/v1/dataproducts/{entity_id}/versions',
        json={'slug': 'orders-curated', 'name': 'Curated Orders v2', 'contract': {}},
    )
    assert version_resp.status_code == 201
    assert version_resp.json()['version'] == 2


@pytest.mark.asyncio
async def test_lineage_is_version_scoped(api_client, fake_principal, async_session):
    from loom.model.datasource import DataSource
    from loom.model.enums import DataSourceKind

    datasource = DataSource(
        tenant_id=fake_principal.tenant_id,
        owner_id=fake_principal.principal_id,
        created_by_id=fake_principal.principal_id,
        slug='orders-db',
        name='Orders DB',
        kind=DataSourceKind.DATABASE,
    )
    async_session.add(datasource)
    await async_session.commit()

    create_resp = await api_client.post(
        '/api/v1/dataproducts', json={'slug': 'orders-curated', 'name': 'Curated Orders', 'contract': {}}
    )
    entity_id = create_resp.json()['entity_id']

    lineage_resp = await api_client.post(
        f'/api/v1/dataproducts/{entity_id}/versions/1/lineage',
        json={'source_datasource_id': str(datasource.id)},
    )
    assert lineage_resp.status_code == 201

    list_resp = await api_client.get(f'/api/v1/dataproducts/{entity_id}/versions/1/lineage')
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/api/catalog/test_dataproduct.py -v`
Expected: FAIL — `404 Not Found` for `/dataproducts`

- [ ] **Step 3: Implement**

```python
# src/loom/api/catalog/dataproduct/schemas.py
import uuid

from pydantic import BaseModel


class DataProductCreateRequest(BaseModel):
    slug: str
    name: str
    description: str | None = None
    contract: dict
    owner_id: uuid.UUID | None = None


class DataProductLineageCreateRequest(BaseModel):
    source_datasource_id: uuid.UUID | None = None
    source_dataproduct_id: uuid.UUID | None = None
```

```python
# src/loom/api/catalog/dataproduct/repository.py
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.model.dataproduct import DataProduct, DataProductLineage
from loom.model.enums import LifecycleState


class DataProductRepository:
    """Async persistence access for DataProduct, scoped by tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> DataProduct | None:
        return await self._session.scalar(
            sa.select(DataProduct).where(
                DataProduct.tenant_id == tenant_id,
                DataProduct.entity_id == entity_id,
                DataProduct.is_current.is_(True),
            )
        )

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> DataProduct | None:
        return await self._session.scalar(
            sa.select(DataProduct).where(
                DataProduct.tenant_id == tenant_id,
                DataProduct.entity_id == entity_id,
                DataProduct.version == version,
            )
        )

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[DataProduct]:
        result = await self._session.scalars(
            sa.select(DataProduct)
            .where(DataProduct.tenant_id == tenant_id, DataProduct.entity_id == entity_id)
            .order_by(DataProduct.version)
        )
        return list(result)

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DataProduct], int]:
        stmt = sa.select(DataProduct).where(
            DataProduct.tenant_id == tenant_id, DataProduct.is_current.is_(True)
        )
        if lifecycle_state is not None:
            stmt = stmt.where(DataProduct.lifecycle_state == lifecycle_state)
        if slug is not None:
            stmt = stmt.where(DataProduct.slug == slug)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(DataProduct.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, dataproduct: DataProduct) -> DataProduct:
        self._session.add(dataproduct)
        await flush_or_raise(self._session)
        return dataproduct

    async def save(self, dataproduct: DataProduct) -> DataProduct:
        await flush_or_raise(self._session)
        return dataproduct

    async def add_lineage(self, lineage: DataProductLineage) -> DataProductLineage:
        self._session.add(lineage)
        await flush_or_raise(self._session)
        return lineage

    async def list_lineage(
        self, dataproduct_version_id: uuid.UUID
    ) -> list[DataProductLineage]:
        result = await self._session.scalars(
            sa.select(DataProductLineage).where(
                DataProductLineage.dataproduct_id == dataproduct_version_id
            )
        )
        return list(result)
```

```python
# src/loom/api/catalog/dataproduct/service.py
import datetime
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError, IllegalTransitionError
from loom.api.catalog.lifecycle import is_legal_transition
from loom.model.dataproduct import DataProduct, DataProductLineage
from loom.model.enums import LifecycleState

from .repository import DataProductRepository
from .schemas import DataProductCreateRequest, DataProductLineageCreateRequest


class DataProductService:
    """Use-cases for the DataProduct aggregate."""

    def __init__(self, repository: DataProductRepository) -> None:
        self._repository = repository

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        data: DataProductCreateRequest,
    ) -> DataProduct:
        dataproduct = DataProduct(
            tenant_id=tenant_id,
            owner_id=data.owner_id or created_by_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            contract=data.contract,
        )
        return await self._repository.add(dataproduct)

    async def get_current(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> DataProduct:
        dataproduct = await self._repository.get_current(tenant_id, entity_id)
        if dataproduct is None:
            raise EntityNotFoundError(f'DataProduct {entity_id} not found')
        return dataproduct

    async def get_version(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> DataProduct:
        dataproduct = await self._repository.get_version(tenant_id, entity_id, version)
        if dataproduct is None:
            detail = f'DataProduct {entity_id} version {version} not found'
            raise EntityNotFoundError(detail)
        return dataproduct

    async def list_versions(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID
    ) -> list[DataProduct]:
        versions = await self._repository.list_versions(tenant_id, entity_id)
        if not versions:
            raise EntityNotFoundError(f'DataProduct {entity_id} not found')
        return versions

    async def list_current(
        self,
        tenant_id: uuid.UUID,
        *,
        lifecycle_state: LifecycleState | None,
        slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DataProduct], int]:
        return await self._repository.list_current(
            tenant_id,
            lifecycle_state=lifecycle_state,
            slug=slug,
            limit=limit,
            offset=offset,
        )

    async def create_new_version(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_id: uuid.UUID,
        entity_id: uuid.UUID,
        data: DataProductCreateRequest,
    ) -> DataProduct:
        current = await self.get_current(tenant_id, entity_id)
        current.is_current = False
        await self._repository.save(current)
        new_version = DataProduct(
            entity_id=entity_id,
            version=current.version + 1,
            is_current=True,
            tenant_id=tenant_id,
            owner_id=data.owner_id or current.owner_id,
            created_by_id=created_by_id,
            slug=data.slug,
            name=data.name,
            description=data.description,
            contract=data.contract,
        )
        return await self._repository.add(new_version)

    async def transition(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        to_state: LifecycleState,
        actor_id: uuid.UUID,
    ) -> DataProduct:
        current = await self.get_current(tenant_id, entity_id)
        if current.version != version:
            detail = f'Version {version} is not the current version of {entity_id}'
            raise IllegalTransitionError(detail)
        if not is_legal_transition(current.lifecycle_state, to_state):
            detail = f'{current.lifecycle_state} -> {to_state} is not a legal transition'
            raise IllegalTransitionError(detail)
        current.lifecycle_state = to_state
        if to_state == LifecycleState.APPROVED:
            current.approved_by_id = actor_id
            current.approved_at = datetime.datetime.now(datetime.UTC)
        return await self._repository.save(current)

    async def add_lineage(
        self,
        *,
        tenant_id: uuid.UUID,
        entity_id: uuid.UUID,
        version: int,
        data: DataProductLineageCreateRequest,
    ) -> DataProductLineage:
        dataproduct = await self.get_version(tenant_id, entity_id, version)
        lineage = DataProductLineage(
            dataproduct_id=dataproduct.id,
            source_datasource_id=data.source_datasource_id,
            source_dataproduct_id=data.source_dataproduct_id,
        )
        return await self._repository.add_lineage(lineage)

    async def list_lineage(
        self, tenant_id: uuid.UUID, entity_id: uuid.UUID, version: int
    ) -> list[DataProductLineage]:
        dataproduct = await self.get_version(tenant_id, entity_id, version)
        return await self._repository.list_lineage(dataproduct.id)
```

```python
# src/loom/api/catalog/dataproduct/router.py
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_current_principal, get_session, require_scopes
from loom.api.catalog.lifecycle import TransitionRequest
from loom.api.catalog.pagination import Page, PaginationParams
from loom.api.catalog.security import AuthenticatedPrincipal
from loom.model.enums import LifecycleState
from loom.model.schemas.dataproduct import DataProductLineageRead, DataProductRead

from .repository import DataProductRepository
from .schemas import DataProductCreateRequest, DataProductLineageCreateRequest
from .service import DataProductService

router = APIRouter(prefix='/dataproducts', tags=['dataproducts'])


def _service(session: AsyncSession = Depends(get_session)) -> DataProductService:
    return DataProductService(DataProductRepository(session))


@router.post('', response_model=DataProductRead, status_code=201)
async def create_dataproduct(
    body: DataProductCreateRequest,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:write')),
):
    return await service.create(
        tenant_id=principal.tenant_id, created_by_id=principal.principal_id, data=body
    )


@router.get('', response_model=Page[DataProductRead])
async def list_dataproducts(
    pagination: PaginationParams = Depends(),
    lifecycle_state: LifecycleState | None = None,
    slug: str | None = None,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:read')),
):
    items, total = await service.list_current(
        principal.tenant_id,
        lifecycle_state=lifecycle_state,
        slug=slug,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(items=items, total=total, limit=pagination.limit, offset=pagination.offset)


@router.get('/{entity_id}', response_model=DataProductRead)
async def get_dataproduct(
    entity_id: uuid.UUID,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:read')),
):
    return await service.get_current(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions', response_model=list[DataProductRead])
async def list_dataproduct_versions(
    entity_id: uuid.UUID,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:read')),
):
    return await service.list_versions(principal.tenant_id, entity_id)


@router.get('/{entity_id}/versions/{version}', response_model=DataProductRead)
async def get_dataproduct_version(
    entity_id: uuid.UUID,
    version: int,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:read')),
):
    return await service.get_version(principal.tenant_id, entity_id, version)


@router.post('/{entity_id}/versions', response_model=DataProductRead, status_code=201)
async def create_dataproduct_version(
    entity_id: uuid.UUID,
    body: DataProductCreateRequest,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:write')),
):
    return await service.create_new_version(
        tenant_id=principal.tenant_id,
        created_by_id=principal.principal_id,
        entity_id=entity_id,
        data=body,
    )


@router.post('/{entity_id}/versions/{version}/transitions', response_model=DataProductRead)
async def transition_dataproduct(
    entity_id: uuid.UUID,
    version: int,
    body: TransitionRequest,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:transition')),
):
    return await service.transition(
        tenant_id=principal.tenant_id,
        entity_id=entity_id,
        version=version,
        to_state=body.to_state,
        actor_id=principal.principal_id,
    )


@router.post(
    '/{entity_id}/versions/{version}/lineage',
    response_model=DataProductLineageRead,
    status_code=201,
)
async def add_dataproduct_lineage(
    entity_id: uuid.UUID,
    version: int,
    body: DataProductLineageCreateRequest,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:write')),
):
    return await service.add_lineage(
        tenant_id=principal.tenant_id, entity_id=entity_id, version=version, data=body
    )


@router.get(
    '/{entity_id}/versions/{version}/lineage', response_model=list[DataProductLineageRead]
)
async def list_dataproduct_lineage(
    entity_id: uuid.UUID,
    version: int,
    service: DataProductService = Depends(_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _scopes: None = Depends(require_scopes('catalog:dataproduct:read')),
):
    return await service.list_lineage(principal.tenant_id, entity_id, version)
```

Modify `src/loom/api/catalog/main.py` — add alongside the existing local
imports/`include_router` calls inside `create_app`:

```python
    from .dataproduct.router import router as dataproduct_router

    app.include_router(dataproduct_router, prefix='/api/v1')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/api/catalog/test_dataproduct.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/api/catalog/dataproduct src/loom/api/catalog/main.py tests/api/catalog/test_dataproduct.py
git commit -m "Add DataProduct module: CRUD, versioning, transitions, lineage"
```

---

## Task 9: Platform tier — Tenant, Principal, Environment

Plain CRUD (create/get/list/update, no `DELETE`, no versioning). Reuses
`loom.model.schemas.{tenant,environment}.*` Create/Read/Update classes
directly as request/response bodies — no new API-specific schemas needed
(`PrincipalCreate`/`EnvironmentCreate` already carry a client-supplied
`tenant_id`, matching the platform-tier design). Auth is
`require_scopes(...)` only — **no** `get_current_principal` dependency
(these tables have no `owner_id`/`created_by_id` to attribute; requiring
principal resolution here would deadlock the very first admin, per the
design spec).

**Files:**
- Create: `src/loom/api/catalog/tenant/__init__.py`
- Create: `src/loom/api/catalog/tenant/repository.py`
- Create: `src/loom/api/catalog/tenant/service.py`
- Create: `src/loom/api/catalog/tenant/router.py`
- Create: `src/loom/api/catalog/principal/__init__.py`
- Create: `src/loom/api/catalog/principal/repository.py`
- Create: `src/loom/api/catalog/principal/service.py`
- Create: `src/loom/api/catalog/principal/router.py`
- Create: `src/loom/api/catalog/environment/__init__.py`
- Create: `src/loom/api/catalog/environment/repository.py`
- Create: `src/loom/api/catalog/environment/service.py`
- Create: `src/loom/api/catalog/environment/router.py`
- Modify: `src/loom/api/catalog/main.py`
- Test: `tests/api/catalog/test_platform.py`

**Interfaces:**
- Consumes: `flush_or_raise`, `EntityNotFoundError`, `get_session`, `require_scopes`, `Page`/`PaginationParams` (Task 2); `TenantCreate`/`TenantRead`/`TenantUpdate`, `PrincipalCreate`/`PrincipalRead`/`PrincipalUpdate`, `EnvironmentCreate`/`EnvironmentRead`/`EnvironmentUpdate` (prior plan's `loom.model.schemas`).
- Produces: three `router`s (mounted below), `TenantService`/`PrincipalService`/`EnvironmentService`.

- [ ] **Step 1: Write the failing tests**

```python
# src/loom/api/catalog/tenant/__init__.py
```
```python
# src/loom/api/catalog/principal/__init__.py
```
```python
# src/loom/api/catalog/environment/__init__.py
```

```python
# tests/api/catalog/test_platform.py
import pytest


@pytest.mark.asyncio
async def test_tenant_crud(api_client):
    create_resp = await api_client.post('/api/v1/tenants', json={'slug': 'acme', 'name': 'Acme Corp'})
    assert create_resp.status_code == 201
    tenant_id = create_resp.json()['id']

    get_resp = await api_client.get(f'/api/v1/tenants/{tenant_id}')
    assert get_resp.status_code == 200
    assert get_resp.json()['slug'] == 'acme'

    list_resp = await api_client.get('/api/v1/tenants')
    assert list_resp.status_code == 200
    assert list_resp.json()['total'] >= 1

    update_resp = await api_client.patch(
        f'/api/v1/tenants/{tenant_id}', json={'name': 'Acme Corp Inc'}
    )
    assert update_resp.status_code == 200
    assert update_resp.json()['name'] == 'Acme Corp Inc'


@pytest.mark.asyncio
async def test_principal_crud(api_client):
    tenant_resp = await api_client.post('/api/v1/tenants', json={'slug': 'globex', 'name': 'Globex'})
    tenant_id = tenant_resp.json()['id']

    create_resp = await api_client.post(
        '/api/v1/principals',
        json={
            'tenant_id': tenant_id,
            'kind': 'user',
            'display_name': 'Ada',
            'external_id': 'ada@globex.example',
        },
    )
    assert create_resp.status_code == 201
    principal_id = create_resp.json()['id']

    get_resp = await api_client.get(f'/api/v1/principals/{principal_id}')
    assert get_resp.status_code == 200

    list_resp = await api_client.get('/api/v1/principals', params={'tenant_id': tenant_id})
    assert list_resp.status_code == 200
    assert list_resp.json()['total'] == 1

    update_resp = await api_client.patch(
        f'/api/v1/principals/{principal_id}', json={'display_name': 'Ada Lovelace'}
    )
    assert update_resp.status_code == 200
    assert update_resp.json()['display_name'] == 'Ada Lovelace'


@pytest.mark.asyncio
async def test_environment_crud(api_client):
    tenant_resp = await api_client.post('/api/v1/tenants', json={'slug': 'initech', 'name': 'Initech'})
    tenant_id = tenant_resp.json()['id']

    create_resp = await api_client.post(
        '/api/v1/environments',
        json={
            'tenant_id': tenant_id,
            'name': 'prod',
            'kind': 'production',
            'compute_boundary_ref': 'vpc-prod-compute',
            'network_boundary_ref': 'vpc-prod-net',
        },
    )
    assert create_resp.status_code == 201
    environment_id = create_resp.json()['id']

    list_resp = await api_client.get('/api/v1/environments', params={'tenant_id': tenant_id})
    assert list_resp.status_code == 200
    assert list_resp.json()['total'] == 1

    update_resp = await api_client.patch(
        f'/api/v1/environments/{environment_id}',
        json={'compute_boundary_ref': 'vpc-prod-compute-v2'},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()['compute_boundary_ref'] == 'vpc-prod-compute-v2'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/api/catalog/test_platform.py -v`
Expected: FAIL — `404 Not Found` for `/tenants`

- [ ] **Step 3: Implement**

```python
# src/loom/api/catalog/tenant/repository.py
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.model.tenant import Tenant


class TenantRepository:
    """Async persistence access for Tenant, platform-wide (not tenant-scoped)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return await self._session.get(Tenant, tenant_id)

    async def list_all(self, *, limit: int, offset: int) -> tuple[list[Tenant], int]:
        total = await self._session.scalar(sa.select(sa.func.count()).select_from(Tenant))
        rows = await self._session.scalars(
            sa.select(Tenant).order_by(Tenant.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, tenant: Tenant) -> Tenant:
        self._session.add(tenant)
        await flush_or_raise(self._session)
        return tenant

    async def save(self, tenant: Tenant) -> Tenant:
        await flush_or_raise(self._session)
        return tenant
```

```python
# src/loom/api/catalog/tenant/service.py
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError
from loom.model.schemas.tenant import TenantCreate, TenantUpdate
from loom.model.tenant import Tenant

from .repository import TenantRepository


class TenantService:
    """Use-cases for the platform-tier Tenant aggregate."""

    def __init__(self, repository: TenantRepository) -> None:
        self._repository = repository

    async def create(self, data: TenantCreate) -> Tenant:
        tenant = Tenant(slug=data.slug, name=data.name)
        return await self._repository.add(tenant)

    async def get(self, tenant_id: uuid.UUID) -> Tenant:
        tenant = await self._repository.get(tenant_id)
        if tenant is None:
            raise EntityNotFoundError(f'Tenant {tenant_id} not found')
        return tenant

    async def list_all(self, *, limit: int, offset: int) -> tuple[list[Tenant], int]:
        return await self._repository.list_all(limit=limit, offset=offset)

    async def update(self, tenant_id: uuid.UUID, data: TenantUpdate) -> Tenant:
        tenant = await self.get(tenant_id)
        if data.name is not None:
            tenant.name = data.name
        return await self._repository.save(tenant)
```

```python
# src/loom/api/catalog/tenant/router.py
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_session, require_scopes
from loom.api.catalog.pagination import Page, PaginationParams
from loom.model.schemas.tenant import TenantCreate, TenantRead, TenantUpdate

from .repository import TenantRepository
from .service import TenantService

router = APIRouter(prefix='/tenants', tags=['tenants'])


def _service(session: AsyncSession = Depends(get_session)) -> TenantService:
    return TenantService(TenantRepository(session))


@router.post('', response_model=TenantRead, status_code=201)
async def create_tenant(
    body: TenantCreate,
    service: TenantService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:tenant:write')),
):
    return await service.create(body)


@router.get('', response_model=Page[TenantRead])
async def list_tenants(
    pagination: PaginationParams = Depends(),
    service: TenantService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:tenant:read')),
):
    items, total = await service.list_all(limit=pagination.limit, offset=pagination.offset)
    return Page(items=items, total=total, limit=pagination.limit, offset=pagination.offset)


@router.get('/{tenant_id}', response_model=TenantRead)
async def get_tenant(
    tenant_id: uuid.UUID,
    service: TenantService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:tenant:read')),
):
    return await service.get(tenant_id)


@router.patch('/{tenant_id}', response_model=TenantRead)
async def update_tenant(
    tenant_id: uuid.UUID,
    body: TenantUpdate,
    service: TenantService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:tenant:write')),
):
    return await service.update(tenant_id, body)
```

```python
# src/loom/api/catalog/principal/repository.py
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.model.tenant import Principal


class PrincipalRepository:
    """Async persistence access for Principal (platform tier)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, principal_id: uuid.UUID) -> Principal | None:
        return await self._session.get(Principal, principal_id)

    async def list_by_tenant(
        self, tenant_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Principal], int]:
        stmt = sa.select(Principal).where(Principal.tenant_id == tenant_id)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(Principal.display_name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, principal: Principal) -> Principal:
        self._session.add(principal)
        await flush_or_raise(self._session)
        return principal

    async def save(self, principal: Principal) -> Principal:
        await flush_or_raise(self._session)
        return principal
```

```python
# src/loom/api/catalog/principal/service.py
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError
from loom.model.schemas.tenant import PrincipalCreate, PrincipalUpdate
from loom.model.tenant import Principal

from .repository import PrincipalRepository


class PrincipalService:
    """Use-cases for the platform-tier Principal aggregate."""

    def __init__(self, repository: PrincipalRepository) -> None:
        self._repository = repository

    async def create(self, data: PrincipalCreate) -> Principal:
        principal = Principal(
            tenant_id=data.tenant_id,
            kind=data.kind,
            display_name=data.display_name,
            external_id=data.external_id,
        )
        return await self._repository.add(principal)

    async def get(self, principal_id: uuid.UUID) -> Principal:
        principal = await self._repository.get(principal_id)
        if principal is None:
            raise EntityNotFoundError(f'Principal {principal_id} not found')
        return principal

    async def list_by_tenant(
        self, tenant_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Principal], int]:
        return await self._repository.list_by_tenant(tenant_id, limit=limit, offset=offset)

    async def update(self, principal_id: uuid.UUID, data: PrincipalUpdate) -> Principal:
        principal = await self.get(principal_id)
        if data.display_name is not None:
            principal.display_name = data.display_name
        return await self._repository.save(principal)
```

```python
# src/loom/api/catalog/principal/router.py
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_session, require_scopes
from loom.api.catalog.pagination import Page, PaginationParams
from loom.model.schemas.tenant import PrincipalCreate, PrincipalRead, PrincipalUpdate

from .repository import PrincipalRepository
from .service import PrincipalService

router = APIRouter(prefix='/principals', tags=['principals'])


def _service(session: AsyncSession = Depends(get_session)) -> PrincipalService:
    return PrincipalService(PrincipalRepository(session))


@router.post('', response_model=PrincipalRead, status_code=201)
async def create_principal(
    body: PrincipalCreate,
    service: PrincipalService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:principal:write')),
):
    return await service.create(body)


@router.get('', response_model=Page[PrincipalRead])
async def list_principals(
    tenant_id: uuid.UUID = Query(...),
    pagination: PaginationParams = Depends(),
    service: PrincipalService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:principal:read')),
):
    items, total = await service.list_by_tenant(
        tenant_id, limit=pagination.limit, offset=pagination.offset
    )
    return Page(items=items, total=total, limit=pagination.limit, offset=pagination.offset)


@router.get('/{principal_id}', response_model=PrincipalRead)
async def get_principal(
    principal_id: uuid.UUID,
    service: PrincipalService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:principal:read')),
):
    return await service.get(principal_id)


@router.patch('/{principal_id}', response_model=PrincipalRead)
async def update_principal(
    principal_id: uuid.UUID,
    body: PrincipalUpdate,
    service: PrincipalService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:principal:write')),
):
    return await service.update(principal_id, body)
```

```python
# src/loom/api/catalog/environment/repository.py
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.db import flush_or_raise
from loom.model.environment import Environment


class EnvironmentRepository:
    """Async persistence access for Environment (platform tier)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, environment_id: uuid.UUID) -> Environment | None:
        return await self._session.get(Environment, environment_id)

    async def list_by_tenant(
        self, tenant_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Environment], int]:
        stmt = sa.select(Environment).where(Environment.tenant_id == tenant_id)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.scalars(
            stmt.order_by(Environment.name).limit(limit).offset(offset)
        )
        return list(rows), total or 0

    async def add(self, environment: Environment) -> Environment:
        self._session.add(environment)
        await flush_or_raise(self._session)
        return environment

    async def save(self, environment: Environment) -> Environment:
        await flush_or_raise(self._session)
        return environment
```

```python
# src/loom/api/catalog/environment/service.py
import uuid

from loom.api.catalog.exceptions import EntityNotFoundError
from loom.model.environment import Environment
from loom.model.schemas.environment import EnvironmentCreate, EnvironmentUpdate

from .repository import EnvironmentRepository


class EnvironmentService:
    """Use-cases for the platform-tier Environment aggregate."""

    def __init__(self, repository: EnvironmentRepository) -> None:
        self._repository = repository

    async def create(self, data: EnvironmentCreate) -> Environment:
        environment = Environment(
            tenant_id=data.tenant_id,
            name=data.name,
            kind=data.kind,
            compute_boundary_ref=data.compute_boundary_ref,
            network_boundary_ref=data.network_boundary_ref,
        )
        return await self._repository.add(environment)

    async def get(self, environment_id: uuid.UUID) -> Environment:
        environment = await self._repository.get(environment_id)
        if environment is None:
            raise EntityNotFoundError(f'Environment {environment_id} not found')
        return environment

    async def list_by_tenant(
        self, tenant_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Environment], int]:
        return await self._repository.list_by_tenant(tenant_id, limit=limit, offset=offset)

    async def update(
        self, environment_id: uuid.UUID, data: EnvironmentUpdate
    ) -> Environment:
        environment = await self.get(environment_id)
        if data.compute_boundary_ref is not None:
            environment.compute_boundary_ref = data.compute_boundary_ref
        if data.network_boundary_ref is not None:
            environment.network_boundary_ref = data.network_boundary_ref
        return await self._repository.save(environment)
```

```python
# src/loom/api/catalog/environment/router.py
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from loom.api.catalog.dependencies import get_session, require_scopes
from loom.api.catalog.pagination import Page, PaginationParams
from loom.model.schemas.environment import (
    EnvironmentCreate,
    EnvironmentRead,
    EnvironmentUpdate,
)

from .repository import EnvironmentRepository
from .service import EnvironmentService

router = APIRouter(prefix='/environments', tags=['environments'])


def _service(session: AsyncSession = Depends(get_session)) -> EnvironmentService:
    return EnvironmentService(EnvironmentRepository(session))


@router.post('', response_model=EnvironmentRead, status_code=201)
async def create_environment(
    body: EnvironmentCreate,
    service: EnvironmentService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:environment:write')),
):
    return await service.create(body)


@router.get('', response_model=Page[EnvironmentRead])
async def list_environments(
    tenant_id: uuid.UUID = Query(...),
    pagination: PaginationParams = Depends(),
    service: EnvironmentService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:environment:read')),
):
    items, total = await service.list_by_tenant(
        tenant_id, limit=pagination.limit, offset=pagination.offset
    )
    return Page(items=items, total=total, limit=pagination.limit, offset=pagination.offset)


@router.get('/{environment_id}', response_model=EnvironmentRead)
async def get_environment(
    environment_id: uuid.UUID,
    service: EnvironmentService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:environment:read')),
):
    return await service.get(environment_id)


@router.patch('/{environment_id}', response_model=EnvironmentRead)
async def update_environment(
    environment_id: uuid.UUID,
    body: EnvironmentUpdate,
    service: EnvironmentService = Depends(_service),
    _scopes: None = Depends(require_scopes('catalog:environment:write')),
):
    return await service.update(environment_id, body)
```

Modify `src/loom/api/catalog/main.py` — add alongside the existing local
imports/`include_router` calls inside `create_app`:

```python
    from .environment.router import router as environment_router
    from .principal.router import router as principal_router
    from .tenant.router import router as tenant_router

    app.include_router(tenant_router, prefix='/api/v1')
    app.include_router(principal_router, prefix='/api/v1')
    app.include_router(environment_router, prefix='/api/v1')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/api/catalog/test_platform.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/api/catalog/tenant src/loom/api/catalog/principal src/loom/api/catalog/environment src/loom/api/catalog/main.py tests/api/catalog/test_platform.py
git commit -m "Add platform tier: Tenant, Principal, Environment CRUD"
```

---

## Task 10: Wire the full app together, console script, README

Every aggregate router currently gets mounted via a **local** import inside
`create_app` (Tasks 3-9 each added one). This task converts all nine to
**top-level** imports (now safe — every module exists), verifies the
complete app together with an end-to-end smoke test hitting all nine
routers' base paths, adds the `loom-catalog-api` console script, and
documents the component in `README.md`.

**Files:**
- Modify: `src/loom/api/catalog/main.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Test: `tests/api/test_main_wiring.py`

**Interfaces:**
- Consumes: all nine `router` objects from Tasks 3-9.
- Produces: no new interfaces — this task is integration/wiring/polish only.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_main_wiring.py
import pytest


@pytest.mark.asyncio
async def test_all_routers_are_mounted(api_client):
    for path in (
        '/api/v1/capabilities',
        '/api/v1/agents',
        '/api/v1/skills',
        '/api/v1/tools',
        '/api/v1/datasources',
        '/api/v1/dataproducts',
        '/api/v1/tenants',
        '/api/v1/principals',
        '/api/v1/environments',
    ):
        params = {'tenant_id': '00000000-0000-0000-0000-000000000000'}
        response = await api_client.get(path, params=params)
        assert response.status_code == 200, f'{path} returned {response.status_code}'


@pytest.mark.asyncio
async def test_missing_token_returns_401(api_client):
    # A request through the raw ASGI app (no dependency override) must 401.
    from httpx import ASGITransport, AsyncClient

    from loom.api.catalog.main import create_app
    from loom.config import RootConfig

    real_app = create_app(RootConfig(config_path='/dev/null'))
    transport = ASGITransport(app=real_app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        response = await client.get('/api/v1/capabilities')
    assert response.status_code in (401, 403)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_main_wiring.py -v`
Expected: FAIL — `test_missing_token_returns_401` passes already (no override,
real auth applies), but the count/shape of routers mounted at `/api/v1/...`
should already mostly pass since Tasks 3-9 mounted their own routers. The
concrete expected failure here is cosmetic (this test is really a
regression guard for Step 3's import refactor) — run it now to record the
baseline, then re-run after Step 3 to confirm nothing broke.

- [ ] **Step 3: Implement**

Rewrite `src/loom/api/catalog/main.py` in full (`Write`, not `Edit` — this
consolidates nine separate local-import edits from Tasks 3-9 into one
clean top-level-import file):

```python
# src/loom/api/catalog/main.py
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
```

Modify `pyproject.toml`'s `[project.scripts]` — add alongside the existing
`loom` entry:

```toml
loom-catalog-api = "loom.api.catalog.main:run"
```

Modify `README.md` — append a new section after the existing "Database"
section:

```markdown
## Catalog API

The Catalog component is a standalone FastAPI service fronting the
Capability/Agent/Skill/Tool/DataSource/DataProduct aggregates, plus a
platform-bootstrap tier (Tenant/Principal/Environment). It requires a
configured database (see above) and an OIDC issuer:

    loom config set auth.issuer https://idp.example/realms/loom
    loom config set auth.audience loom-catalog-api

Run it:

    loom-catalog-api

Or directly via uvicorn for development: `uvicorn loom.api.catalog.main:app --reload`.

Every request requires a Bearer JWT carrying a `tenant_id` claim and
either a `scope` claim (space-separated `catalog:{resource}:{action}`
scopes) or a `roles` claim (`catalog-viewer`, `catalog-editor`,
`catalog-approver`, `catalog-admin`, `catalog-platform-admin`). See
`docs/superpowers/specs/2026-08-08-catalog-api-design.md` for the full
access-rights model.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/api/test_main_wiring.py -v`
Expected: PASS (2 passed)

Then run the FULL suite to confirm nothing broke across all nine aggregate
modules from the import refactor:

Run: `pytest -v`
Expected: all tests pass (this plan's tests plus the prior plan's tests,
all green together).

- [ ] **Step 5: Commit**

```bash
git add src/loom/api/catalog/main.py pyproject.toml README.md tests/api/test_main_wiring.py
git commit -m "Wire full Catalog app together, add console script and README"
```

---

## Task 11: IdP abstraction — `IdpAdminClient` protocol and role definitions

Provider-agnostic types. No Keycloak-specific code here (that's Task 12).

**Files:**
- Create: `src/loom/idp/client.py`
- Test: `tests/idp/test_client.py`

**Interfaces:**
- Consumes: `content_scopes`, `platform_scopes`, `ROLE_BUNDLES` (Task 2 `catalog_roles.py`).
- Produces: `loom.idp.client.{IdpAdminClient, ClientRegistrationResult, RoleDefinition, catalog_role_definitions}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/idp/test_client.py
from loom.idp.client import IdpAdminClient, catalog_role_definitions


def test_catalog_role_definitions_has_29_entries():
    roles = catalog_role_definitions()
    assert len(roles) == 29
    leaf = [r for r in roles if not r.composite_of]
    composite = [r for r in roles if r.composite_of]
    assert len(leaf) == 24
    assert len(composite) == 5


def test_composite_roles_only_reference_known_leaf_names():
    roles = catalog_role_definitions()
    leaf_names = {r.name for r in roles if not r.composite_of}
    for role in roles:
        if role.composite_of:
            assert set(role.composite_of) <= leaf_names


class _FakeIdpAdminClient:
    async def register_client(self, *, client_id, client_name, service_account):
        del client_id, client_name, service_account
        return None

    async def declare_client_roles(self, client_ref, roles):
        del client_ref, roles


def test_fake_client_satisfies_the_protocol():
    client: IdpAdminClient = _FakeIdpAdminClient()
    assert isinstance(client, _FakeIdpAdminClient)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/idp/test_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.idp.client'`

- [ ] **Step 3: Implement**

```python
# src/loom/idp/client.py
import dataclasses
from typing import Protocol

from .catalog_roles import ROLE_BUNDLES, content_scopes, platform_scopes


@dataclasses.dataclass(frozen=True)
class ClientRegistrationResult:
    """Result of registering a new OAuth client with the IDP."""

    client_id: str
    internal_ref: str
    registration_access_token: str | None


@dataclasses.dataclass(frozen=True)
class RoleDefinition:
    """One role to declare under a registered client, IDP-agnostic."""

    name: str
    description: str
    composite_of: tuple[str, ...] = ()


class IdpAdminClient(Protocol):
    """Provider-agnostic admin operations: client registration, roles."""

    async def register_client(
        self, *, client_id: str, client_name: str, service_account: bool
    ) -> ClientRegistrationResult: ...

    async def declare_client_roles(
        self, client_ref: str, roles: list[RoleDefinition]
    ) -> None: ...


def catalog_role_definitions() -> list[RoleDefinition]:
    """Build the 24 leaf-scope roles plus the 5 composite bundle roles."""
    leaf_roles = [
        RoleDefinition(name=scope, description=f'Grants {scope}')
        for scope in sorted(content_scopes() | platform_scopes())
    ]
    composite_roles = [
        RoleDefinition(
            name=name,
            description=f'Bundle role: {name}',
            composite_of=tuple(sorted(scopes)),
        )
        for name, scopes in ROLE_BUNDLES.items()
    ]
    return leaf_roles + composite_roles
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/idp/test_client.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/idp/client.py tests/idp/test_client.py
git commit -m "Add IdpAdminClient protocol and catalog role definitions"
```

---

## Task 12: KeycloakAdminClient

The concrete Keycloak implementation of `IdpAdminClient`. Tested entirely
against `httpx.MockTransport` — no real Keycloak instance, no real network
calls.

**Files:**
- Create: `src/loom/idp/keycloak.py`
- Test: `tests/idp/test_keycloak.py`

**Interfaces:**
- Consumes: `IdpAdminClient`, `ClientRegistrationResult`, `RoleDefinition`, `catalog_role_definitions` (Task 11).
- Produces: `loom.idp.keycloak.KeycloakAdminClient(issuer, token, *, transport=None)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/idp/test_keycloak.py
import json

import httpx
import pytest

from loom.idp.catalog_roles import content_scopes, platform_scopes
from loom.idp.client import catalog_role_definitions
from loom.idp.keycloak import KeycloakAdminClient


def test_derive_realm_admin_base():
    client = KeycloakAdminClient(issuer='https://idp.example/realms/loom', token='t')
    assert client._realm_admin_base == 'https://idp.example/admin/realms/loom'


@pytest.mark.asyncio
async def test_register_client_and_declare_roles():
    created_roles: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == '/realms/loom/clients-registrations/openid-connect':
            body = {'client_id': 'loom-catalog-api', 'registration_access_token': 'rat'}
            return httpx.Response(201, json=body)
        if path == '/admin/realms/loom/clients' and request.method == 'GET':
            return httpx.Response(200, json=[{'id': 'internal-uuid-123'}])
        if (
            path == '/admin/realms/loom/clients/internal-uuid-123/roles'
            and request.method == 'POST'
        ):
            created_roles.append(json.loads(request.read())['name'])
            return httpx.Response(201)
        if (
            path.startswith('/admin/realms/loom/clients/internal-uuid-123/roles/')
            and request.method == 'GET'
            and not path.endswith('/composites')
        ):
            role_name = path.rsplit('/', 1)[-1]
            return httpx.Response(200, json={'id': f'id-{role_name}', 'name': role_name})
        if path.endswith('/composites') and request.method == 'POST':
            return httpx.Response(204)
        raise AssertionError(f'Unexpected request: {request.method} {path}')

    transport = httpx.MockTransport(handler)
    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom', token='t', transport=transport
    )

    result = await client.register_client(
        client_id='loom-catalog-api', client_name='Loom Catalog API', service_account=True
    )
    assert result.client_id == 'loom-catalog-api'
    assert result.internal_ref == 'internal-uuid-123'
    assert result.registration_access_token == 'rat'

    await client.declare_client_roles(result.internal_ref, catalog_role_definitions())

    all_scopes = content_scopes() | platform_scopes()
    assert all_scopes <= set(created_roles)
    assert 'catalog-viewer' in created_roles
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/idp/test_keycloak.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.idp.keycloak'`

- [ ] **Step 3: Implement**

```python
# src/loom/idp/keycloak.py
import httpx

from .client import ClientRegistrationResult, RoleDefinition


class KeycloakAdminClient:
    """IdpAdminClient implementation for Keycloak."""

    def __init__(
        self,
        issuer: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._issuer = issuer.rstrip('/')
        self._token = token
        self._realm_admin_base = self._derive_realm_admin_base(issuer)
        self._transport = transport

    @staticmethod
    def _derive_realm_admin_base(issuer: str) -> str:
        """Derive the Admin REST API base URL from a realm issuer URL."""
        base, _, realm = issuer.rstrip('/').rpartition('/realms/')
        return f'{base}/admin/realms/{realm}'

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport)

    async def register_client(
        self, *, client_id: str, client_name: str, service_account: bool
    ) -> ClientRegistrationResult:
        """Register via Dynamic Client Registration, resolve the internal id."""
        payload = {
            'clientId': client_id,
            'name': client_name,
            'serviceAccountsEnabled': service_account,
            'standardFlowEnabled': not service_account,
            'publicClient': False,
            'directAccessGrantsEnabled': False,
        }
        headers = {'Authorization': f'Bearer {self._token}'}
        async with self._client() as http:
            response = await http.post(
                f'{self._issuer}/clients-registrations/openid-connect',
                json=payload,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            body = response.json()

            lookup = await http.get(
                f'{self._realm_admin_base}/clients',
                params={'clientId': client_id},
                headers=headers,
                timeout=30.0,
            )
            lookup.raise_for_status()
            matches = lookup.json()

        if not matches:
            detail = f'Registered client {client_id} not found via Admin API lookup'
            raise RuntimeError(detail)

        return ClientRegistrationResult(
            client_id=body['client_id'],
            internal_ref=matches[0]['id'],
            registration_access_token=body.get('registration_access_token'),
        )

    async def declare_client_roles(
        self, client_ref: str, roles: list[RoleDefinition]
    ) -> None:
        """Create leaf roles, then composite roles with their associations."""
        leaf_roles = [role for role in roles if not role.composite_of]
        composite_roles = [role for role in roles if role.composite_of]
        headers = {'Authorization': f'Bearer {self._token}'}
        base = f'{self._realm_admin_base}/clients/{client_ref}/roles'

        async with self._client() as http:
            for role in leaf_roles:
                response = await http.post(
                    base,
                    json={'name': role.name, 'description': role.description},
                    headers=headers,
                    timeout=30.0,
                )
                response.raise_for_status()

            for role in composite_roles:
                response = await http.post(
                    base,
                    json={'name': role.name, 'description': role.description},
                    headers=headers,
                    timeout=30.0,
                )
                response.raise_for_status()

                resolved_sub_roles = []
                for sub_role_name in role.composite_of:
                    sub_response = await http.get(
                        f'{base}/{sub_role_name}', headers=headers, timeout=30.0
                    )
                    sub_response.raise_for_status()
                    resolved_sub_roles.append(sub_response.json())

                composite_response = await http.post(
                    f'{base}/{role.name}/composites',
                    json=resolved_sub_roles,
                    headers=headers,
                    timeout=30.0,
                )
                composite_response.raise_for_status()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/idp/test_keycloak.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/loom/idp/keycloak.py tests/idp/test_keycloak.py
git commit -m "Add KeycloakAdminClient implementation"
```

---

## Task 13: `loom idp register-client` CLI command

**Files:**
- Create: `src/loom/cli/idp.py`
- Modify: `src/loom/cli/main.py`
- Modify: `README.md`
- Test: `tests/cli/test_idp.py`

**Interfaces:**
- Consumes: `KeycloakAdminClient` (Task 12), `catalog_role_definitions` (Task 11), `RootConfig`/`config.auth.issuer` (prior plan + Task 1).
- Produces: `loom.cli.idp.idp_register_client(config: RootConfig, args: argparse.Namespace) -> int`; `loom idp register-client` CLI surface.

- [ ] **Step 1: Write the failing tests**

```python
# tests/cli/test_idp.py
import argparse

import pytest

from loom.cli.idp import idp_register_client
from loom.config import RootConfig
from loom.idp.client import ClientRegistrationResult


class _FakeKeycloakAdminClient:
    def __init__(self, issuer, token, **kwargs):
        del kwargs
        self.issuer = issuer
        self.token = token
        self.declared_roles = None

    async def register_client(self, *, client_id, client_name, service_account):
        del client_name, service_account
        return ClientRegistrationResult(
            client_id=client_id,
            internal_ref='fake-internal-id',
            registration_access_token='fake-rat',
        )

    async def declare_client_roles(self, client_ref, roles):
        self.declared_roles = (client_ref, roles)


@pytest.mark.asyncio
async def test_idp_register_client_wires_arguments(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    args = argparse.Namespace(
        issuer_url='https://idp.example/realms/loom',
        token='initial-access-token',
        client_id='loom-catalog-api',
        client_name=None,
    )

    result = await idp_register_client(config, args)
    assert result == 0

    output = capsys.readouterr().out
    assert 'loom-catalog-api' in output
    assert 'fake-rat' in output
    assert 'Declared 29 roles' in output


@pytest.mark.asyncio
async def test_idp_register_client_requires_issuer(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    args = argparse.Namespace(issuer_url=None, token='t', client_id='x', client_name=None)
    result = await idp_register_client(config, args)
    assert result == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/cli/test_idp.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loom.cli.idp'`

- [ ] **Step 3: Implement**

```python
# src/loom/cli/idp.py
import argparse

from loom.config import RootConfig
from loom.idp.client import catalog_role_definitions
from loom.idp.keycloak import KeycloakAdminClient


async def idp_register_client(config: RootConfig, args: argparse.Namespace) -> int:
    """Register the Catalog OAuth client and declare its role vocabulary."""
    issuer_url = args.issuer_url or config.auth.issuer
    if not issuer_url:
        print('No --issuer-url given and config.auth.issuer is unset.')
        return 1

    client_name = args.client_name or args.client_id
    client = KeycloakAdminClient(issuer=issuer_url, token=args.token)
    result = await client.register_client(
        client_id=args.client_id, client_name=client_name, service_account=True
    )

    roles = catalog_role_definitions()
    await client.declare_client_roles(result.internal_ref, roles)

    print(f'Registered client: {result.client_id} (internal ref: {result.internal_ref})')
    if result.registration_access_token:
        print('Registration access token (store securely, shown once):')
        print(result.registration_access_token)
    print(f'Declared {len(roles)} roles under the client.')
    return 0
```

Modify `src/loom/cli/main.py`:

Add the import alongside the existing `from loom.cli.db import ...` line:

```python
from loom.cli.idp import idp_register_client
```

Add the subparser wiring inside `main()`, alongside the existing `db_parser` block:

```python
        idp_parser = subparsers.add_parser('idp', help='Identity provider bootstrap commands')
        idp_subparser = idp_parser.add_subparsers(required=True)

        idp_register_parser = idp_subparser.add_parser(
            'register-client',
            help='Register the Catalog OAuth client and declare its roles',
        )
        idp_register_parser.add_argument(
            '--issuer-url',
            dest='issuer_url',
            default=None,
            help='OIDC realm issuer URL, defaults to config.auth.issuer',
        )
        idp_register_parser.add_argument(
            '--token', required=True, help='IDP admin/initial access token'
        )
        idp_register_parser.add_argument(
            '--client-id',
            dest='client_id',
            required=True,
            help='OAuth client ID to register',
        )
        idp_register_parser.add_argument(
            '--client-name',
            dest='client_name',
            default=None,
            help='Human-readable client name, defaults to --client-id',
        )
        idp_register_parser.set_defaults(func=idp_register_client)
```

Modify `README.md` — append after the "Catalog API" section added in Task 10:

```markdown
### Registering the client with your IDP

For Keycloak: generate a token capable of both Dynamic Client Registration
and realm role management (a plain "Initial Access Token" from Client
Registration settings is DCR-only and typically insufficient — use an
access token from a realm-admin service account via `client_credentials`),
then:

    loom idp register-client --issuer-url https://idp.example/realms/loom \
      --token <token> --client-id loom-catalog-api

This registers the OAuth client and declares all 24 leaf scopes plus the 5
composite roles (`catalog-viewer`, `catalog-editor`, `catalog-approver`,
`catalog-admin`, `catalog-platform-admin`) as roles under that client.
Assigning those roles to actual users/service accounts is a separate step
performed in the Keycloak admin console.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/cli/test_idp.py -v`
Expected: PASS (2 passed)

Then run the full suite one final time — this is the last task in the plan:

Run: `pytest -v`
Expected: all tests pass (this plan's tests plus the prior plan's, all green).

Run: `ruff format src/loom/cli/idp.py src/loom/idp tests/cli/test_idp.py tests/idp && ruff check src/loom/cli/idp.py src/loom/idp tests/cli/test_idp.py tests/idp`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/loom/cli/idp.py src/loom/cli/main.py README.md tests/cli/test_idp.py
git commit -m "Add loom idp register-client CLI command"
```

---

## Self-Review Notes

- **Spec coverage:** every section of the design spec maps to a task —
  DDD layering (Task 2's shared infra + Tasks 3-9's Router/Service/Repository
  modules), async engine (Task 1), auth/scopes/roles (Task 2's security.py +
  Task 2's catalog_roles.py), versioning semantics (Tasks 3-8's
  create/get/list/new-version/transition endpoints), platform bootstrap
  (Task 9), IDP integration (Tasks 11-13).
- **Placeholder scan:** no TBD/TODO; every step has runnable code and an
  exact command.
- **Type consistency:** `flush_or_raise`, `EntityNotFoundError`,
  `IllegalTransitionError`, `is_legal_transition`, `TransitionRequest`,
  `get_session`/`get_current_principal`/`require_scopes`,
  `Page`/`PaginationParams` are imported with identical names and
  signatures across every task that consumes them (Tasks 3-9 all import
  from `loom.api.catalog.{db,exceptions,lifecycle,dependencies,pagination}`
  without renaming). The `add`/`save` repository split and version-scoped
  sub-resource pattern (FK to the specific version row's `.id`, looked up
  via `get_version`, not `get_current`) is applied identically in Tasks 3,
  5, 6, 8.
- **Empirically verified before writing this plan** (via scratch scripts,
  not part of any task): SQLAlchemy's async engine requires `greenlet`
  (not pulled in by plain `sqlalchemy>=2.0.36`); `PRAGMA foreign_keys=ON`
  on an async aiosqlite engine must be registered on `engine.sync_engine`
  specifically (not globally, not via an `isinstance(dbapi_connection,
  sqlite3.Connection)` guard — the async adapter isn't that type);
  `create_async_engine('sqlite+aiosqlite:///:memory:')` defaults to
  `StaticPool`, so concurrently-open sessions correctly share the same
  in-memory database with no extra configuration needed.
- **Deviation from the physical-model plan, flagged and deliberate:**
  lifecycle transitions mutate `lifecycle_state`/`approved_by_id`/
  `approved_at` in place on the current version row (a second sanctioned
  mutable field alongside `is_current`), rather than requiring a new
  version row per transition. This was flagged to and confirmed by the
  user during brainstorming — conflating governance-state changes with
  content versioning would inflate version numbers on every approval step.
  No schema/migration change was needed for this (no DB-level constraint
  forbade it).
