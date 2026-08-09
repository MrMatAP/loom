# Keycloak Admin-Credentials Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `loom idp register-client`'s Dynamic Client
Registration / Initial Access Token flow with genuine Keycloak Admin REST
API access, authenticated via admin username/password.

**Architecture:** `KeycloakAdminClient` gains a `login()` async classmethod
that performs a Resource Owner Password Credentials grant and returns a
ready client; `register_client`/`declare_client_roles` move fully onto
`/admin/realms/{realm}/...` endpoints. The `IdpAdminClient` protocol's
method signatures are unchanged. The CLI collects admin credentials
(flag → env var → hidden prompt precedence) and calls `login()` before
proceeding.

**Tech Stack:** httpx (async, `httpx.MockTransport` for tests), truststore
(system trust store for TLS verification), pytest-asyncio, argparse,
getpass.

## Global Constraints

- Docstrings: short, single-line, ≤88 characters including the 4-space
  indent and both triple-quote sets.
- No comments on obvious code; comments only where the *why* is
  non-obvious.
- Type hints on all function signatures.
- Ruff: single quotes, 88-char line length, target Python 3.14+.
- Scope discipline: run `ruff format`/`ruff check` only on the files a
  task creates or modifies — never bare across `src/loom` or `tests`.
- Never commit build/test artifacts; stage explicitly by path.
- Run the full test suite (`pytest -v`, or `uv run pytest -v` in this repo)
  after each task and confirm no regressions.

---

### Task 1: `KeycloakAdminClient` — admin login + full Admin API

**Files:**
- Modify: `src/loom/idp/client.py` (add `client_secret` field to
  `ClientRegistrationResult`)
- Modify: `src/loom/idp/keycloak.py` (full rewrite of `register_client`,
  `declare_client_roles`'s signature, new `login()` classmethod)
- Test: `tests/idp/test_keycloak.py` (full rewrite)

**Interfaces:**
- Consumes: `RoleDefinition` (`src/loom/idp/client.py`, unchanged).
- Produces: `KeycloakAdminClient.login(issuer, *, username, password, admin_realm='master', admin_client_id='admin-cli', transport=None) -> KeycloakAdminClient`
  (new), `KeycloakAdminClient(issuer, token, *, transport=None)`
  (unchanged constructor shape), `register_client(...) -> ClientRegistrationResult`
  (unchanged signature, new internals), `declare_client_roles(client_ref: str, roles: list[RoleDefinition]) -> None`
  (signature reverted to match `IdpAdminClient` protocol — no more
  3-argument `(client_id, rat, roles)`), `ClientRegistrationResult.client_secret: str | None = None`
  (new field; `registration_access_token` stays but is always `None` from
  this Keycloak implementation now).

- [ ] **Step 1: Add `client_secret` to `ClientRegistrationResult`**

In `src/loom/idp/client.py`, change:

```python
@dataclasses.dataclass(frozen=True)
class ClientRegistrationResult:
    """Result of registering a new OAuth client with the IDP."""

    client_id: str
    internal_ref: str
    registration_access_token: str | None
```

to:

```python
@dataclasses.dataclass(frozen=True)
class ClientRegistrationResult:
    """Result of registering a new OAuth client with the IDP."""

    client_id: str
    internal_ref: str
    registration_access_token: str | None
    client_secret: str | None = None
```

Nothing else in this file changes.

- [ ] **Step 2: Replace `src/loom/idp/keycloak.py` in full**

```python
import getpass
import ssl

import httpx
import truststore

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
        self._base = self._derive_base(issuer)
        self._realm_admin_base = self._derive_realm_admin_base(issuer)
        self._transport = transport

    @staticmethod
    def _derive_base(issuer: str) -> str:
        """Strip the `/realms/{realm}` suffix, leaving the server root URL."""
        base, _, _ = issuer.rstrip('/').rpartition('/realms/')
        return base

    @classmethod
    def _derive_realm_admin_base(cls, issuer: str) -> str:
        """Derive the Admin REST API base URL from a realm issuer URL."""
        base, _, realm = issuer.rstrip('/').rpartition('/realms/')
        return f'{base}/admin/realms/{realm}'

    def _client(self) -> httpx.AsyncClient:
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return httpx.AsyncClient(transport=self._transport, verify=ctx)

    @staticmethod
    def _raise_unless_already_exists(response: httpx.Response) -> None:
        """Treat Keycloak's 409 as success so bootstrap stays re-runnable."""
        if response.status_code == httpx.codes.CONFLICT:
            return
        response.raise_for_status()

    @classmethod
    async def login(
        cls,
        issuer: str,
        *,
        username: str,
        password: str,
        admin_realm: str = 'master',
        admin_client_id: str = 'admin-cli',
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> 'KeycloakAdminClient':
        """Authenticate as a Keycloak admin and return a ready client."""
        base = cls._derive_base(issuer)
        token_url = f'{base}/realms/{admin_realm}/protocol/openid-connect/token'
        data = {
            'grant_type': 'password',
            'client_id': admin_client_id,
            'username': username,
            'password': password,
        }
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        async with httpx.AsyncClient(transport=transport, verify=ctx) as http:
            response = await http.post(token_url, data=data, timeout=30.0)
            response.raise_for_status()
            access_token = response.json()['access_token']

        return cls(issuer=issuer, token=access_token, transport=transport)

    async def register_client(
        self, *, client_id: str, client_name: str, service_account: bool
    ) -> ClientRegistrationResult:
        """Create the client via the Admin API; idempotent on 409."""
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
                f'{self._realm_admin_base}/clients',
                json=payload,
                headers=headers,
                timeout=30.0,
            )
            if response.status_code == httpx.codes.CONFLICT:
                internal_ref = await self._lookup_client_id(http, client_id, headers)
            else:
                response.raise_for_status()
                location = response.headers['Location']
                internal_ref = location.rstrip('/').rsplit('/', 1)[-1]

            secret = await self._fetch_client_secret(http, internal_ref, headers)

        return ClientRegistrationResult(
            client_id=client_id,
            internal_ref=internal_ref,
            registration_access_token=None,
            client_secret=secret,
        )

    async def _lookup_client_id(
        self, http: httpx.AsyncClient, client_id: str, headers: dict[str, str]
    ) -> str:
        """Resolve an existing client's internal id by its clientId."""
        response = await http.get(
            f'{self._realm_admin_base}/clients',
            params={'clientId': client_id},
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        matches = response.json()
        if not matches:
            detail = f'Client {client_id} not found after a 409 on creation'
            raise RuntimeError(detail)
        return matches[0]['id']

    async def _fetch_client_secret(
        self, http: httpx.AsyncClient, internal_ref: str, headers: dict[str, str]
    ) -> str | None:
        """Fetch the generated secret for a confidential client, if any."""
        response = await http.get(
            f'{self._realm_admin_base}/clients/{internal_ref}/client-secret',
            headers=headers,
            timeout=30.0,
        )
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        response.raise_for_status()
        return response.json().get('value')

    async def declare_client_roles(
        self, client_ref: str, roles: list[RoleDefinition]
    ) -> None:
        """Create leaf then composite roles; idempotent across bootstrap re-runs."""
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
                self._raise_unless_already_exists(response)

            for role in composite_roles:
                response = await http.post(
                    base,
                    json={'name': role.name, 'description': role.description},
                    headers=headers,
                    timeout=30.0,
                )
                self._raise_unless_already_exists(response)

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
                self._raise_unless_already_exists(composite_response)
```

- [ ] **Step 3: Replace `tests/idp/test_keycloak.py` in full**

```python
import json

import httpx
import pytest

from loom.idp.catalog_roles import content_scopes, platform_scopes
from loom.idp.client import catalog_role_definitions
from loom.idp.keycloak import KeycloakAdminClient


def test_derive_realm_admin_base():
    client = KeycloakAdminClient(issuer='https://idp.example/realms/loom', token='t')
    assert client._realm_admin_base == 'https://idp.example/admin/realms/loom'
    assert client._base == 'https://idp.example'


@pytest.mark.asyncio
async def test_login_posts_password_grant_and_returns_ready_client():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/realms/master/protocol/openid-connect/token'
        captured.update(dict(httpx.QueryParams(request.read().decode())))
        return httpx.Response(200, json={'access_token': 'admin-token-xyz'})

    client = await KeycloakAdminClient.login(
        'https://idp.example/realms/loom',
        username='admin',
        password='hunter2',
        transport=httpx.MockTransport(handler),
    )

    assert captured['grant_type'] == 'password'
    assert captured['client_id'] == 'admin-cli'
    assert captured['username'] == 'admin'
    assert captured['password'] == 'hunter2'
    assert client._token == 'admin-token-xyz'
    assert client._issuer == 'https://idp.example/realms/loom'


@pytest.mark.asyncio
async def test_login_honors_admin_realm_and_client_id_overrides():
    seen_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        body = dict(httpx.QueryParams(request.read().decode()))
        assert body['client_id'] == 'custom-admin-cli'
        return httpx.Response(200, json={'access_token': 't'})

    await KeycloakAdminClient.login(
        'https://idp.example/realms/loom',
        username='admin',
        password='hunter2',
        admin_realm='internal-admins',
        admin_client_id='custom-admin-cli',
        transport=httpx.MockTransport(handler),
    )

    assert seen_paths == ['/realms/internal-admins/protocol/openid-connect/token']


@pytest.mark.asyncio
async def test_register_client_creates_and_fetches_secret():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == '/admin/realms/loom/clients' and request.method == 'POST':
            return httpx.Response(
                201,
                headers={
                    'Location': (
                        'https://idp.example/admin/realms/loom/clients/'
                        'internal-uuid-123'
                    )
                },
            )
        if (
            path == '/admin/realms/loom/clients/internal-uuid-123/client-secret'
            and request.method == 'GET'
        ):
            return httpx.Response(200, json={'type': 'secret', 'value': 'shh-secret'})
        raise AssertionError(f'Unexpected request: {request.method} {path}')

    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom',
        token='t',
        transport=httpx.MockTransport(handler),
    )

    result = await client.register_client(
        client_id='loom-catalog-api',
        client_name='Loom Catalog API',
        service_account=True,
    )

    assert result.client_id == 'loom-catalog-api'
    assert result.internal_ref == 'internal-uuid-123'
    assert result.client_secret == 'shh-secret'
    assert result.registration_access_token is None


@pytest.mark.asyncio
async def test_register_client_is_idempotent_on_409():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == '/admin/realms/loom/clients' and request.method == 'POST':
            return httpx.Response(409, json={'errorMessage': 'Client already exists'})
        if path == '/admin/realms/loom/clients' and request.method == 'GET':
            assert dict(request.url.params) == {'clientId': 'loom-catalog-api'}
            return httpx.Response(200, json=[{'id': 'internal-uuid-123'}])
        if (
            path == '/admin/realms/loom/clients/internal-uuid-123/client-secret'
            and request.method == 'GET'
        ):
            return httpx.Response(200, json={'value': 'shh-secret'})
        raise AssertionError(f'Unexpected request: {request.method} {path}')

    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom',
        token='t',
        transport=httpx.MockTransport(handler),
    )

    result = await client.register_client(
        client_id='loom-catalog-api',
        client_name='Loom Catalog API',
        service_account=True,
    )
    assert result.internal_ref == 'internal-uuid-123'


@pytest.mark.asyncio
async def test_register_client_tolerates_missing_secret():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == '/admin/realms/loom/clients' and request.method == 'POST':
            return httpx.Response(
                201,
                headers={
                    'Location': (
                        'https://idp.example/admin/realms/loom/clients/'
                        'internal-uuid-123'
                    )
                },
            )
        if (
            path == '/admin/realms/loom/clients/internal-uuid-123/client-secret'
            and request.method == 'GET'
        ):
            return httpx.Response(404)
        raise AssertionError(f'Unexpected request: {request.method} {path}')

    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom',
        token='t',
        transport=httpx.MockTransport(handler),
    )

    result = await client.register_client(
        client_id='loom-catalog-api',
        client_name='Loom Catalog API',
        service_account=False,
    )
    assert result.client_secret is None


@pytest.mark.asyncio
async def test_declare_client_roles_creates_leaf_then_composite_roles():
    created_roles: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
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
            return httpx.Response(
                200, json={'id': f'id-{role_name}', 'name': role_name}
            )
        if path.endswith('/composites') and request.method == 'POST':
            return httpx.Response(204)
        raise AssertionError(f'Unexpected request: {request.method} {path}')

    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom',
        token='t',
        transport=httpx.MockTransport(handler),
    )

    await client.declare_client_roles('internal-uuid-123', catalog_role_definitions())

    all_scopes = content_scopes() | platform_scopes()
    assert all_scopes <= set(created_roles)
    assert 'catalog-viewer' in created_roles


@pytest.mark.asyncio
async def test_declare_client_roles_is_idempotent_on_409():
    attempted_roles: list[str] = []
    attempted_composites: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if (
            path == '/admin/realms/loom/clients/internal-uuid-123/roles'
            and request.method == 'POST'
        ):
            attempted_roles.append(json.loads(request.read())['name'])
            return httpx.Response(409, json={'errorMessage': 'Role already exists'})
        if path.endswith('/composites') and request.method == 'POST':
            attempted_composites.append(path)
            return httpx.Response(409, json={'errorMessage': 'Already associated'})
        if (
            path.startswith('/admin/realms/loom/clients/internal-uuid-123/roles/')
            and request.method == 'GET'
        ):
            role_name = path.rsplit('/', 1)[-1]
            return httpx.Response(
                200, json={'id': f'id-{role_name}', 'name': role_name}
            )
        raise AssertionError(f'Unexpected request: {request.method} {path}')

    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom',
        token='t',
        transport=httpx.MockTransport(handler),
    )

    await client.declare_client_roles('internal-uuid-123', catalog_role_definitions())

    all_scopes = content_scopes() | platform_scopes()
    assert all_scopes <= set(attempted_roles)
    assert attempted_composites


@pytest.mark.asyncio
async def test_declare_client_roles_still_raises_on_server_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={'errorMessage': 'boom'})

    client = KeycloakAdminClient(
        issuer='https://idp.example/realms/loom',
        token='t',
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.declare_client_roles(
            'internal-uuid-123', catalog_role_definitions()
        )
```

- [ ] **Step 4: Run this task's tests**

Run: `uv run pytest tests/idp/test_keycloak.py -v`
Expected: all tests PASS (9 tests: `test_derive_realm_admin_base`,
`test_login_posts_password_grant_and_returns_ready_client`,
`test_login_honors_admin_realm_and_client_id_overrides`,
`test_register_client_creates_and_fetches_secret`,
`test_register_client_is_idempotent_on_409`,
`test_register_client_tolerates_missing_secret`,
`test_declare_client_roles_creates_leaf_then_composite_roles`,
`test_declare_client_roles_is_idempotent_on_409`,
`test_declare_client_roles_still_raises_on_server_error`).

- [ ] **Step 5: Format, lint, run the full suite, self-review, commit**

```bash
ruff format src/loom/idp/client.py src/loom/idp/keycloak.py tests/idp/test_keycloak.py
ruff check src/loom/idp/client.py src/loom/idp/keycloak.py tests/idp/test_keycloak.py
uv run pytest -v
git add src/loom/idp/client.py src/loom/idp/keycloak.py tests/idp/test_keycloak.py
git commit -m "Refactor KeycloakAdminClient onto admin-credential Admin API auth"
```

Self-review before committing: confirm every `httpx.AsyncClient(` in
`keycloak.py` passes `transport=` (either `self._transport` inside
`_client()`, or the `transport` parameter directly inside `login()`) — no
construction should silently drop injectability. Confirm no stray
`getpass` import landed in `keycloak.py`. Confirm docstring lengths with
`awk '{print length}'`.

---

### Task 2: CLI — admin-credential flags and README

**Files:**
- Modify: `src/loom/cli/idp.py` (full rewrite)
- Modify: `src/loom/cli/main.py:223-225` (replace the `--token` argument
  block with four new admin-credential arguments)
- Modify: `README.md` (rewrite the "Registering the client with your IDP"
  section)
- Test: `tests/cli/test_idp.py` (full rewrite)

**Interfaces:**
- Consumes: `KeycloakAdminClient.login(...)` and the unchanged
  `register_client`/`declare_client_roles` from Task 1.
- Produces: nothing consumed by later work — this is the last task.

- [ ] **Step 1: Replace `src/loom/cli/idp.py` in full**

```python
import argparse
import getpass
import os

from loom.config import RootConfig
from loom.idp.client import catalog_role_definitions
from loom.idp.keycloak import KeycloakAdminClient


def _resolve_admin_username(args: argparse.Namespace) -> str:
    """Resolve the admin username from flag, env var, or an interactive prompt."""
    if args.admin_username:
        return args.admin_username
    env_value = os.environ.get('LOOM_IDP_ADMIN_USERNAME')
    if env_value:
        return env_value
    return input('Keycloak admin username: ')


def _resolve_admin_password(args: argparse.Namespace) -> str:
    """Resolve the admin password from flag, env var, or a hidden prompt."""
    if args.admin_password:
        return args.admin_password
    env_value = os.environ.get('LOOM_IDP_ADMIN_PASSWORD')
    if env_value:
        return env_value
    return getpass.getpass('Keycloak admin password: ')


async def idp_register_client(config: RootConfig, args: argparse.Namespace) -> int:
    """Register the Catalog OAuth client and declare its role vocabulary."""
    issuer_url = args.issuer_url or config.auth.issuer
    if not issuer_url:
        print('No --issuer-url given and config.auth.issuer is unset.')
        return 1

    username = _resolve_admin_username(args)
    password = _resolve_admin_password(args)
    client = await KeycloakAdminClient.login(
        issuer_url,
        username=username,
        password=password,
        admin_realm=args.admin_realm,
        admin_client_id=args.admin_client_id,
    )

    client_name = args.client_name or args.client_id
    result = await client.register_client(
        client_id=args.client_id, client_name=client_name, service_account=True
    )

    roles = catalog_role_definitions()
    await client.declare_client_roles(result.internal_ref, roles)

    print(
        f'Registered client: {result.client_id} (internal ref: {result.internal_ref})'
    )
    if result.client_secret:
        print('Client secret (store securely, shown once):')
        print(result.client_secret)
    print(f'Declared {len(roles)} roles under the client.')
    return 0
```

- [ ] **Step 2: Update `src/loom/cli/main.py`'s argparse wiring**

Find this block (currently around line 223-225):

```python
        idp_register_parser.add_argument(
            '--token', required=True, help='IDP admin/initial access token'
        )
```

Replace it with:

```python
        idp_register_parser.add_argument(
            '--admin-username',
            dest='admin_username',
            default=None,
            help='Keycloak admin username, else LOOM_IDP_ADMIN_USERNAME or a prompt',
        )
        idp_register_parser.add_argument(
            '--admin-password',
            dest='admin_password',
            default=None,
            help='Keycloak admin password, else LOOM_IDP_ADMIN_PASSWORD or a prompt',
        )
        idp_register_parser.add_argument(
            '--admin-realm',
            dest='admin_realm',
            default='master',
            help='Realm to authenticate the admin user against',
        )
        idp_register_parser.add_argument(
            '--admin-client-id',
            dest='admin_client_id',
            default='admin-cli',
            help='Public client used for the admin login grant',
        )
```

Everything else in `main.py` (the `--issuer-url`, `--client-id`,
`--client-name` arguments and `idp_register_parser.set_defaults(func=idp_register_client)`)
stays exactly as-is.

- [ ] **Step 3: Replace `tests/cli/test_idp.py` in full**

```python
import argparse

import pytest

from loom.cli.idp import idp_register_client
from loom.config import RootConfig
from loom.idp.client import ClientRegistrationResult


class _FakeKeycloakAdminClient:
    def __init__(self, issuer, token):
        self.issuer = issuer
        self.token = token
        self.declared_roles = None

    @classmethod
    async def login(cls, issuer, **kwargs):
        del kwargs
        return cls(issuer, 'fake-admin-token')

    async def register_client(self, *, client_id, client_name, service_account):
        del client_name, service_account
        return ClientRegistrationResult(
            client_id=client_id,
            internal_ref='fake-internal-id',
            registration_access_token=None,
            client_secret='fake-secret',
        )

    async def declare_client_roles(self, client_ref, roles):
        self.declared_roles = (client_ref, roles)


def _base_args(**overrides):
    defaults = dict(
        issuer_url='https://idp.example/realms/loom',
        admin_username='admin',
        admin_password='hunter2',
        admin_realm='master',
        admin_client_id='admin-cli',
        client_id='loom-catalog-api',
        client_name=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.mark.asyncio
async def test_idp_register_client_wires_arguments(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _FakeKeycloakAdminClient)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await idp_register_client(config, _base_args())
    assert result == 0

    output = capsys.readouterr().out
    assert 'loom-catalog-api' in output
    assert 'fake-secret' in output
    assert 'Declared 29 roles' in output


@pytest.mark.asyncio
async def test_idp_register_client_requires_issuer(tmp_path):
    config = RootConfig(config_path=tmp_path / 'config.yaml')
    result = await idp_register_client(config, _base_args(issuer_url=None))
    assert result == 1


@pytest.mark.asyncio
async def test_admin_username_env_var_fallback(monkeypatch, tmp_path):
    captured = {}

    class _SpyKeycloakAdminClient(_FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del kwargs
            captured['username'] = username
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)
    monkeypatch.setenv('LOOM_IDP_ADMIN_USERNAME', 'env-admin')
    monkeypatch.delenv('LOOM_IDP_ADMIN_PASSWORD', raising=False)

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register_client(config, _base_args(admin_username=None))
    assert captured['username'] == 'env-admin'


@pytest.mark.asyncio
async def test_admin_password_prompted_when_flag_and_env_both_absent(
    monkeypatch, tmp_path
):
    captured = {}

    class _SpyKeycloakAdminClient(_FakeKeycloakAdminClient):
        @classmethod
        async def login(cls, issuer, *, username, password, **kwargs):
            del kwargs
            captured['password'] = password
            return cls(issuer, 'fake-admin-token')

    monkeypatch.setattr('loom.cli.idp.KeycloakAdminClient', _SpyKeycloakAdminClient)
    monkeypatch.delenv('LOOM_IDP_ADMIN_PASSWORD', raising=False)
    monkeypatch.setattr('loom.cli.idp.getpass.getpass', lambda prompt: 'prompted-pw')

    config = RootConfig(config_path=tmp_path / 'config.yaml')
    await idp_register_client(config, _base_args(admin_password=None))
    assert captured['password'] == 'prompted-pw'
```

- [ ] **Step 4: Update `README.md`'s "Registering the client with your IDP" section**

Find the current section (search for `### Registering the client with your IDP`)
and replace its body with:

```markdown
### Registering the client with your IDP

For Keycloak, authenticate as an admin (the standard Keycloak superadmin
realm is `master`):

    loom idp register-client --issuer-url https://idp.example/realms/loom \
      --client-id loom-catalog-api

You'll be prompted for the admin username and password. To avoid the
prompt, set `LOOM_IDP_ADMIN_USERNAME`/`LOOM_IDP_ADMIN_PASSWORD`, or pass
`--admin-username`/`--admin-password` directly (flags take precedence over
env vars, which take precedence over the prompt). If the admin account
lives in a different realm, or the deployment uses a different admin
client than the default `admin-cli`, pass `--admin-realm`/`--admin-client-id`.

This registers the OAuth client via the Keycloak Admin REST API and
declares all 24 leaf scopes plus the 5 composite roles (`catalog-viewer`,
`catalog-editor`, `catalog-approver`, `catalog-admin`,
`catalog-platform-admin`) as roles under that client, printing the
generated client secret (store it securely — it is shown once). Assigning
those roles to actual users/service accounts is a separate step performed
in the Keycloak admin console.
```

- [ ] **Step 5: Run this task's tests**

Run: `uv run pytest tests/cli/test_idp.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 6: Format, lint, run the full suite, self-review, commit**

```bash
ruff format src/loom/cli/idp.py src/loom/cli/main.py tests/cli/test_idp.py
ruff check src/loom/cli/idp.py src/loom/cli/main.py tests/cli/test_idp.py
uv run pytest -v
git add src/loom/cli/idp.py src/loom/cli/main.py tests/cli/test_idp.py README.md
git commit -m "Switch loom idp register-client to admin-credential auth"
```

Self-review before committing: confirm `--token` no longer appears
anywhere in `main.py` or `README.md`. Confirm the new `--admin-*` flags
are reachable via `python -c "...sys.argv=['loom','idp','register-client','--help']..."`
or equivalent. Confirm docstring lengths with `awk '{print length}'`.
