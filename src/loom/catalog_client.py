import typing
import uuid

import httpx

from loom.tls import build_ssl_context


class CatalogApiError(RuntimeError):
    """Raised when the Catalog API rejects a request; carries enough of the
    response to let a caller print something more useful than a stack
    trace."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f'{status_code}: {detail}')


def _error_detail(response: httpx.Response) -> str:
    """Every error shape the Catalog API emits: `register_exception_handlers`'
    domain-exception envelope (`message`), FastAPI's own `HTTPException`
    envelope (`detail`), or -- if neither -- the raw response body."""
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict):
        for key in ('message', 'detail'):
            if key in body:
                return str(body[key])
    return str(body)


class CatalogClient:
    """Thin authenticated async HTTP client for the Loom Catalog API."""

    def __init__(
        self,
        api_base_url: str,
        access_token: str,
        *,
        tenant_id: uuid.UUID | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = api_base_url.rstrip('/')
        self._token = access_token
        # Which Tenant `tenant_path()` below nests a request under --
        # every resource but Tenant itself lives at
        # `/api/v1/tenants/{tenant_id}/...` now, so this is required for
        # those, unused for Tenant's own (flat) routes.
        self._tenant_id = tenant_id
        self._transport = transport

    def tenant_path(self, suffix: str) -> str:
        """Build `/api/v1/tenants/{tenant_id}{suffix}` -- every resource but
        Tenant itself is nested under a specific Tenant (see `loom auth
        set-tenant`/`--tenant-id`), since the same identity may hold a
        Principal in more than one and the server resolves access strictly
        against whichever Tenant is named in the URL, not a token claim or
        header hint."""
        if self._tenant_id is None:
            raise CatalogApiError(
                0, 'tenant_path() called without a tenant_id configured'
            )
        return f'/api/v1/tenants/{self._tenant_id}{suffix}'

    def _client(self) -> httpx.AsyncClient:
        ctx = build_ssl_context()
        return httpx.AsyncClient(transport=self._transport, verify=ctx)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, typing.Any] | None = None,
        json: dict[str, typing.Any] | None = None,
    ) -> dict:
        """Issue one request; returns the decoded JSON body on 2xx, raises
        `CatalogApiError` otherwise. `params`/`json` drop `None` values so
        callers can pass every optional filter unconditionally rather than
        each building a trimmed dict by hand."""
        clean_params = (
            {k: v for k, v in params.items() if v is not None} if params else None
        )
        headers = {'Authorization': f'Bearer {self._token}'}
        async with self._client() as http:
            response = await http.request(
                method,
                f'{self._base}{path}',
                params=clean_params,
                json=json,
                headers=headers,
                timeout=30.0,
            )
        if response.is_error:
            raise CatalogApiError(response.status_code, _error_detail(response))
        return response.json()

    async def get(self, path: str, params: dict[str, typing.Any] | None = None) -> dict:
        """GET `path`; returns the decoded JSON body on 2xx, raises
        `CatalogApiError` otherwise."""
        return await self._request('GET', path, params=params)

    async def post(self, path: str, payload: dict[str, typing.Any]) -> dict:
        """POST `payload` to `path`; returns the decoded JSON body on 2xx,
        raises `CatalogApiError` otherwise."""
        return await self._request('POST', path, json=payload)

    async def patch(self, path: str, payload: dict[str, typing.Any]) -> dict:
        """PATCH `payload` to `path`; returns the decoded JSON body on 2xx,
        raises `CatalogApiError` otherwise. Used by Tenant/Principal updates,
        which mutate in place rather than creating a new version the way
        Capability/ModelEndpoint/Agent's `update` does."""
        return await self._request('PATCH', path, json=payload)
