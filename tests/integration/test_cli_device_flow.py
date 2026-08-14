"""Tier 2 (admin-gated): verifies `loom auth login`'s device flow against a
real Keycloak instance.

Unlike Swagger's Authorization Code flow, the device flow's "pending"
branch is reachable without a browser at all -- RFC 8628 defines
`authorization_pending` precisely for the window before a human visits the
verification URL. That's the assertion below: it proves the device
endpoint's request/response shape end to end (client registration ->
`DeviceCodeClient.start()` -> real HTTP -> `DeviceCodeClient.poll()`'s
first iteration) against this instance, not just against a mock.

What this suite does NOT cover: completing the flow (a human visiting
`verification_uri` and approving it). See the README's "Verifying
interactively" note for that one-time manual check.
"""

from dataclasses import replace

import httpx
import pytest

from loom.idp.device_flow import DeviceCodeClient, DeviceCodeError
from loom.tls import build_ssl_context

from .live import (
    ADMIN_PASSWORD_ENV_VAR,
    ADMIN_USERNAME_ENV_VAR,
    ISSUER_ENV_VAR,
    requires_env_vars,
)

pytestmark = [
    pytest.mark.live_idp,
    requires_env_vars(ISSUER_ENV_VAR, ADMIN_USERNAME_ENV_VAR, ADMIN_PASSWORD_ENV_VAR),
]


@pytest.mark.asyncio
async def test_cli_client_is_registered_for_device_flow_only(admin_client, cli_client):
    client_id, internal_ref = cli_client
    async with httpx.AsyncClient(verify=build_ssl_context()) as http:
        response = await http.get(
            f'{admin_client._realm_admin_base}/clients/{internal_ref}',
            headers={'Authorization': f'Bearer {admin_client._token}'},
            timeout=30.0,
        )
    response.raise_for_status()
    representation = response.json()

    assert representation['clientId'] == client_id
    assert representation['publicClient'] is True
    assert representation['standardFlowEnabled'] is False
    assert representation['directAccessGrantsEnabled'] is False
    assert (
        representation['attributes']['oauth2.device.authorization.grant.enabled']
        == 'true'
    )


@pytest.mark.asyncio
async def test_device_flow_start_and_first_poll_against_live_keycloak(
    live_issuer, cli_client
):
    client_id, _ = cli_client
    device_client = DeviceCodeClient(live_issuer, client_id)

    authorization = await device_client.start()
    assert authorization.device_code
    assert authorization.user_code
    assert authorization.verification_uri

    # No human has visited verification_uri yet, so the real token endpoint
    # must report the RFC 8628 pending state -- proving the request shape,
    # HTTP round trip, and response parsing all work against this instance,
    # short of the one step that needs a browser. `expires_in ==
    # interval` collapses `poll()`'s attempt budget to exactly one real
    # request instead of waiting out the real multi-minute window for a
    # result we already know (nobody will approve this code).
    single_attempt = replace(authorization, expires_in=authorization.interval)
    with pytest.raises(DeviceCodeError, match='timed out'):
        await device_client.poll(single_attempt)


@pytest.mark.asyncio
async def test_set_access_token_lifespan_overrides_client_attribute(
    admin_client, cli_client
):
    """`loom idp register --access-token-lifespan` end to end against real
    Keycloak -- confirms the merge-not-replace GET/PUT doesn't drop
    `oauth2.device.authorization.grant.enabled` (set at registration,
    asserted above) while applying the override."""
    _, internal_ref = cli_client

    await admin_client.set_access_token_lifespan(internal_ref, 1800)

    async with httpx.AsyncClient(verify=build_ssl_context()) as http:
        response = await http.get(
            f'{admin_client._realm_admin_base}/clients/{internal_ref}',
            headers={'Authorization': f'Bearer {admin_client._token}'},
            timeout=30.0,
        )
    response.raise_for_status()
    attributes = response.json()['attributes']

    assert attributes['access.token.lifespan'] == '1800'
    assert attributes['oauth2.device.authorization.grant.enabled'] == 'true'
