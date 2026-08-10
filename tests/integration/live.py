"""Env-var gating shared by the `live_idp` integration suite.

These tests exercise a real Keycloak instance over the network -- they are
excluded from the default `pytest` run by self-skipping (not by pytest
collection args) whenever the env vars below aren't set, so `pytest` stays
green for anyone without live credentials. Run them explicitly with:

    pytest tests/integration/ -m live_idp
"""

import os
import typing

import pytest

if typing.TYPE_CHECKING:
    from loom.idp.keycloak import KeycloakAdminClient

ISSUER_ENV_VAR = 'LOOM_IDP_ISSUER_URL'
ADMIN_USERNAME_ENV_VAR = 'LOOM_IDP_ISSUER_ADMIN_USERNAME'
ADMIN_PASSWORD_ENV_VAR = 'LOOM_IDP_ISSUER_ADMIN_PASSWORD'

# Distinctive prefix for every object this suite creates in the live realm,
# so a run is trivially recognizable (and safe to hand-delete) against
# whatever else lives in the same Keycloak instance. Every fixture that
# creates one tears it down at the end of its scope regardless.
IT_PREFIX = 'loom-it'


def admin_headers(admin_client: KeycloakAdminClient) -> dict[str, str]:
    return {'Authorization': f'Bearer {admin_client._token}'}


def missing_env_vars(*names: str) -> list[str]:
    return [name for name in names if not os.environ.get(name)]


def requires_env_vars(*names: str) -> pytest.MarkDecorator:
    """Skip a module's tests unless every named env var is set. The reason
    names exactly which ones are missing, rather than leaving a bare
    "skipped" for someone to go re-derive."""
    missing = missing_env_vars(*names)
    return pytest.mark.skipif(
        bool(missing), reason=f'missing env var(s): {", ".join(missing)}'
    )
