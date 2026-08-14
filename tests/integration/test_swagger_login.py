"""Tier 2 (admin-gated): verifies the pieces of Swagger UI's interactive
login that are ours to get wrong -- the client registration and the
FastAPI/OpenAPI wiring `create_app()` builds from it.

What this suite does NOT cover: actually driving a browser through
Keycloak's login form. That page is Keycloak's, not ours, and isn't worth
scraping (fragile across Keycloak versions/consent settings) -- see the
README's "Verifying interactively" note for the one-time manual check.
"""

import httpx
import pytest

from loom.api.catalog.main import create_app
from loom.config import RootConfig
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
async def test_swagger_client_is_registered_as_a_pkce_public_client(
    admin_client, swagger_client
):
    client_id, internal_ref = swagger_client
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
    assert representation['standardFlowEnabled'] is True
    assert representation['directAccessGrantsEnabled'] is False
    assert representation['attributes']['pkce.code.challenge.method'] == 'S256'
    assert (
        'https://loom-it.invalid/docs/oauth2-redirect'
        in (representation['redirectUris'])
    )


def test_swagger_ui_oauth_config_matches_the_live_discovery_document(
    live_issuer, live_discovery_document, swagger_client
):
    """What Swagger UI's "Authorize" button actually does: read
    `swagger_ui_init_oauth`/the OpenAPI security scheme `create_app()`
    builds, and confirm they point at this instance's real endpoints --
    the part of a browser login that's ours, not Keycloak's login form."""
    client_id, _ = swagger_client
    config = RootConfig(config_path='/dev/null')
    config.auth.issuer = live_issuer
    config.auth.swagger_client_id = client_id

    app = create_app(config)

    assert app.swagger_ui_init_oauth == {
        'clientId': client_id,
        'usePkceWithAuthorizationCodeGrant': True,
    }
    flow = app.openapi()['components']['securitySchemes'][
        'OAuth2AuthorizationCodeBearer'
    ]['flows']['authorizationCode']
    assert flow['authorizationUrl'] == live_discovery_document['authorization_endpoint']
    assert flow['tokenUrl'] == live_discovery_document['token_endpoint']
