"""Shared fake `KeycloakAdminClient` for `test_idp.py`/`test_idp_public_clients.py`.

`idp_register`/`idp_unregister` always run all four steps (API, MCP,
Swagger UI, CLI) in one call, so any fake standing in for
`loom.cli.idp.KeycloakAdminClient` needs every method the real client
offers, regardless of which step a given test is actually interested in --
hence one shared, fully-featured fake rather than a partial one per file.
"""

from loom.idp.client import ClientRegistrationResult


class FakeKeycloakAdminClient:
    def __init__(self, issuer, token):
        self.issuer = issuer
        self.token = token
        self.registered_confidential_clients: list[dict] = []
        self.registered_public_clients: list[dict] = []
        self.declared_roles: list[tuple[str, list]] = []
        self.audience_mappers: list[tuple[str, str]] = []
        self.client_roles_mappers: list[tuple[str, str]] = []
        self.access_token_lifespan: tuple[str, int] | None = None
        self.deleted_clients: list[str] = []
        self._existing_client_ids: set[str] = set()

    @classmethod
    async def login(cls, issuer, **kwargs):
        del kwargs
        return cls(issuer, 'fake-admin-token')

    @staticmethod
    def _internal_ref(client_id: str) -> str:
        """Deterministic per-client-id ref, so tests can assert which
        client a mapper landed on without threading return values around."""
        return f'fake-internal-ref-{client_id}'

    async def register_client(self, *, client_id, client_name, service_account):
        self._existing_client_ids.add(client_id)
        self.registered_confidential_clients.append(
            {
                'client_id': client_id,
                'client_name': client_name,
                'service_account': service_account,
            }
        )
        return ClientRegistrationResult(
            client_id=client_id,
            internal_ref=self._internal_ref(client_id),
            registration_access_token=None,
            client_secret=f'fake-secret-{client_id}',
        )

    async def register_public_client(self, *, client_id, client_name, **kwargs):
        self._existing_client_ids.add(client_id)
        self.registered_public_clients.append(
            {'client_id': client_id, 'client_name': client_name, **kwargs}
        )
        return ClientRegistrationResult(
            client_id=client_id,
            internal_ref=self._internal_ref(client_id),
            registration_access_token=None,
            client_secret=None,
        )

    async def declare_client_roles(self, client_ref, roles):
        self.declared_roles.append((client_ref, roles))

    async def add_audience_mapper(self, client_ref, *, target_client_id):
        self.audience_mappers.append((client_ref, target_client_id))

    async def add_client_roles_mapper(self, client_ref, *, source_client_id):
        self.client_roles_mappers.append((client_ref, source_client_id))

    async def set_access_token_lifespan(self, client_ref, seconds):
        self.access_token_lifespan = (client_ref, seconds)

    async def delete_client(self, *, client_id):
        self.deleted_clients.append(client_id)
        existed = client_id in self._existing_client_ids
        self._existing_client_ids.discard(client_id)
        return existed
