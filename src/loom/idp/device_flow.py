import asyncio
import dataclasses
from collections.abc import Awaitable, Callable

import httpx

from loom.tls import build_ssl_context


@dataclasses.dataclass(frozen=True)
class DeviceFlowEndpoints:
    """The two endpoints (out of a full `OidcDiscoveryDocument`) the
    Device Authorization Grant actually needs -- resolved once via
    discovery by the caller (`cli/auth.py`'s `auth_login`), never assumed
    to sit at Keycloak's conventional `/protocol/openid-connect/...`
    paths."""

    device_authorization_endpoint: str
    token_endpoint: str


@dataclasses.dataclass(frozen=True)
class DeviceAuthorization:
    """A pending device-code login, as returned by the IDP's device endpoint."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int
    interval: int


@dataclasses.dataclass(frozen=True)
class DeviceTokens:
    """Tokens obtained once the user completes a device-code login."""

    access_token: str
    refresh_token: str | None
    expires_in: int


class DeviceCodeError(RuntimeError):
    """Raised when the IDP denies, times out, or errors a device-code login."""


class DeviceCodeClient:
    """OAuth2 Device Authorization Grant (RFC 8628).

    Used by the `loom` CLI itself to authenticate interactively without a
    local browser redirect target, unlike the Swagger UI's Authorization
    Code + PKCE flow. Takes resolved endpoints (`DeviceFlowEndpoints`),
    never a bare issuer -- the caller is responsible for resolving them via
    OIDC discovery first (`idp/discovery.py`), so this stays IdP-agnostic
    rather than assuming Keycloak's URL conventions.
    """

    def __init__(
        self,
        endpoints: DeviceFlowEndpoints,
        client_id: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._endpoints = endpoints
        self._client_id = client_id
        self._transport = transport
        self._sleep = sleep

    def _client(self) -> httpx.AsyncClient:
        ctx = build_ssl_context()
        return httpx.AsyncClient(transport=self._transport, verify=ctx)

    async def start(self) -> DeviceAuthorization:
        """Request a device/user code pair; the caller displays it to the user."""
        async with self._client() as http:
            response = await http.post(
                self._endpoints.device_authorization_endpoint,
                data={'client_id': self._client_id},
                timeout=30.0,
            )
            response.raise_for_status()
            body = response.json()
        return DeviceAuthorization(
            device_code=body['device_code'],
            user_code=body['user_code'],
            verification_uri=body.get('verification_uri', ''),
            verification_uri_complete=body.get('verification_uri_complete'),
            expires_in=body['expires_in'],
            interval=body.get('interval', 5),
        )

    async def poll(self, authorization: DeviceAuthorization) -> DeviceTokens:
        """Poll the token endpoint until the user authorizes, denies, or expires."""
        interval = authorization.interval
        # `expires_in // interval` approximates the attempt budget; a
        # `slow_down` bump below stretches the real wall-clock time this
        # takes without shrinking the attempt count, which is fine -- it
        # only makes the client slightly more patient than the IDP required.
        attempts = max(1, authorization.expires_in // interval)
        async with self._client() as http:
            for attempt in range(attempts):
                if attempt > 0:
                    await self._sleep(interval)
                response = await http.post(
                    self._endpoints.token_endpoint,
                    data={
                        'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                        'device_code': authorization.device_code,
                        'client_id': self._client_id,
                    },
                    timeout=30.0,
                )
                body = response.json()
                if response.status_code == httpx.codes.OK:
                    return DeviceTokens(
                        access_token=body['access_token'],
                        refresh_token=body.get('refresh_token'),
                        expires_in=body['expires_in'],
                    )
                error = body.get('error')
                if error == 'authorization_pending':
                    continue
                if error == 'slow_down':
                    interval += 5
                    continue
                detail = (
                    body.get('error_description')
                    or error
                    or (f'HTTP {response.status_code}')
                )
                raise DeviceCodeError(f'Device login failed: {detail}')
        raise DeviceCodeError('Device login timed out waiting for user authorization')
