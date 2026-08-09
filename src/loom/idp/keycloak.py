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
    ) -> KeycloakAdminClient:
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
