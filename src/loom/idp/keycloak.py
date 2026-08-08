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
