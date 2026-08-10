import typing

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
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = api_base_url.rstrip('/')
        self._token = access_token
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        ctx = build_ssl_context()
        return httpx.AsyncClient(transport=self._transport, verify=ctx)

    async def post(self, path: str, payload: dict[str, typing.Any]) -> dict:
        """POST `payload` to `path`; returns the decoded JSON body on 2xx,
        raises `CatalogApiError` otherwise."""
        async with self._client() as http:
            response = await http.post(
                f'{self._base}{path}',
                json=payload,
                headers={'Authorization': f'Bearer {self._token}'},
                timeout=30.0,
            )
        if response.is_error:
            raise CatalogApiError(response.status_code, _error_detail(response))
        return response.json()
