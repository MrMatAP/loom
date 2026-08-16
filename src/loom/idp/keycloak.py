import httpx

from loom.tls import build_ssl_context

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
        ctx = build_ssl_context()
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
        ctx = build_ssl_context()
        async with httpx.AsyncClient(transport=transport, verify=ctx) as http:
            response = await http.post(token_url, data=data, timeout=30.0)
            response.raise_for_status()
            body = response.json()
            if 'access_token' not in body:
                detail = (
                    f'Keycloak token response had no access_token '
                    f'(HTTP {response.status_code})'
                )
                raise RuntimeError(detail)
            access_token = body['access_token']

        return cls(issuer=issuer, token=access_token, transport=transport)

    async def register_client(
        self, *, client_id: str, client_name: str, service_account: bool
    ) -> ClientRegistrationResult:
        """Create a confidential client via the Admin API; idempotent on 409."""
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
            internal_ref = await self._create_or_reuse_client(
                http, payload, client_id, headers
            )
            secret = await self._fetch_client_secret(http, internal_ref, headers)

        return ClientRegistrationResult(
            client_id=client_id,
            internal_ref=internal_ref,
            registration_access_token=None,
            client_secret=secret,
        )

    async def register_public_client(
        self,
        *,
        client_id: str,
        client_name: str,
        standard_flow: bool = False,
        device_flow: bool = False,
        redirect_uris: tuple[str, ...] = (),
        web_origins: tuple[str, ...] = (),
    ) -> ClientRegistrationResult:
        """Create a secret-less public client (browser PKCE and/or device
        flow) via the Admin API; idempotent on 409."""
        attributes = {
            'oauth2.device.authorization.grant.enabled': str(device_flow).lower(),
        }
        if standard_flow:
            # PKCE only applies to the Authorization Code flow. Setting it
            # unconditionally also affects the Device Authorization
            # endpoint on some Keycloak versions -- a device-flow-only
            # client (no `standard_flow`) then gets rejected with
            # `invalid_request: Missing parameter: code_challenge_method`,
            # since `DeviceCodeClient` never sends PKCE parameters.
            attributes['pkce.code.challenge.method'] = 'S256'
        payload = {
            'clientId': client_id,
            'name': client_name,
            'publicClient': True,
            'serviceAccountsEnabled': False,
            'standardFlowEnabled': standard_flow,
            'directAccessGrantsEnabled': False,
            'redirectUris': list(redirect_uris),
            'webOrigins': list(web_origins),
            'attributes': attributes,
        }
        headers = {'Authorization': f'Bearer {self._token}'}
        async with self._client() as http:
            internal_ref = await self._create_or_reuse_client(
                http, payload, client_id, headers
            )

        return ClientRegistrationResult(
            client_id=client_id,
            internal_ref=internal_ref,
            registration_access_token=None,
            client_secret=None,
        )

    async def set_access_token_lifespan(self, client_ref: str, seconds: int) -> None:
        """Override this client's access-token lifespan (Keycloak's
        `access.token.lifespan` client attribute), independent of whatever
        the realm's own default is -- deliberately a client-level override
        rather than a realm-wide change, since a realm default is often
        shared with other, unrelated clients. Safe to call on an
        already-registered client (merges into its existing `attributes`
        rather than replacing them), unlike `register_public_client`, which
        no-ops on an existing client via its idempotent-on-409 create path."""
        headers = {'Authorization': f'Bearer {self._token}'}
        async with self._client() as http:
            get_response = await http.get(
                f'{self._realm_admin_base}/clients/{client_ref}',
                headers=headers,
                timeout=30.0,
            )
            get_response.raise_for_status()
            client = get_response.json()
            attributes = client.get('attributes') or {}
            attributes['access.token.lifespan'] = str(seconds)
            client['attributes'] = attributes
            put_response = await http.put(
                f'{self._realm_admin_base}/clients/{client_ref}',
                json=client,
                headers=headers,
                timeout=30.0,
            )
            put_response.raise_for_status()

    async def add_audience_mapper(
        self, client_ref: str, *, target_client_id: str
    ) -> None:
        """Add a protocol mapper so tokens issued to this client also carry
        `target_client_id` in `aud`, satisfying the resource server's audience
        check. Idempotent on 409."""
        await self._add_protocol_mapper(
            client_ref,
            payload={
                'name': f'audience-{target_client_id}',
                'protocol': 'openid-connect',
                'protocolMapper': 'oidc-audience-mapper',
                'consentRequired': False,
                'config': {
                    'included.client.audience': target_client_id,
                    'id.token.claim': 'false',
                    'access.token.claim': 'true',
                },
            },
        )

    async def add_client_roles_mapper(
        self, client_ref: str, *, source_client_id: str
    ) -> None:
        """Add a protocol mapper that flattens `source_client_id`'s client
        roles into a top-level `roles` claim -- the shape
        `security.expand_claims_to_scopes` reads. Without this, a token
        carries roles nested under `resource_access.{client}.roles` instead,
        and every scope check on it fails closed. Idempotent on 409."""
        await self._add_protocol_mapper(
            client_ref,
            payload={
                'name': f'client-roles-{source_client_id}',
                'protocol': 'openid-connect',
                'protocolMapper': 'oidc-usermodel-client-role-mapper',
                'consentRequired': False,
                'config': {
                    'usermodel.clientRoleMapping.clientId': source_client_id,
                    'claim.name': 'roles',
                    'jsonType.label': 'String',
                    'multivalued': 'true',
                    'id.token.claim': 'false',
                    'access.token.claim': 'true',
                },
            },
        )

    async def _add_protocol_mapper(self, client_ref: str, *, payload: dict) -> None:
        """POST a protocol mapper definition to a client; idempotent on 409."""
        headers = {'Authorization': f'Bearer {self._token}'}
        async with self._client() as http:
            response = await http.post(
                f'{self._realm_admin_base}/clients/{client_ref}/protocol-mappers/models',
                json=payload,
                headers=headers,
                timeout=30.0,
            )
            self._raise_unless_already_exists(response)

    async def _create_or_reuse_client(
        self,
        http: httpx.AsyncClient,
        payload: dict,
        client_id: str,
        headers: dict[str, str],
    ) -> str:
        """POST a client creation payload; resolve the existing one on 409."""
        response = await http.post(
            f'{self._realm_admin_base}/clients',
            json=payload,
            headers=headers,
            timeout=30.0,
        )
        if response.status_code == httpx.codes.CONFLICT:
            return await self._lookup_client_id(http, client_id, headers)
        response.raise_for_status()
        if 'Location' not in response.headers:
            detail = (
                f'Keycloak client creation response had no Location '
                f'header (HTTP {response.status_code})'
            )
            raise RuntimeError(detail)
        location = response.headers['Location']
        return location.rstrip('/').rsplit('/', 1)[-1]

    async def _find_client_ref(
        self, http: httpx.AsyncClient, client_id: str, headers: dict[str, str]
    ) -> str | None:
        """Resolve an existing client's internal id by its clientId, or
        `None` if no such client exists."""
        response = await http.get(
            f'{self._realm_admin_base}/clients',
            params={'clientId': client_id},
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        matches = response.json()
        return matches[0]['id'] if matches else None

    async def _lookup_client_id(
        self, http: httpx.AsyncClient, client_id: str, headers: dict[str, str]
    ) -> str:
        """Resolve an existing client's internal id by its clientId --
        raises if it's missing, unlike `_find_client_ref`, since every
        caller of this one just hit a 409 telling it the client exists."""
        internal_ref = await self._find_client_ref(http, client_id, headers)
        if internal_ref is None:
            detail = f'Client {client_id} not found after a 409 on creation'
            raise RuntimeError(detail)
        return internal_ref

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

    async def delete_client(self, *, client_id: str) -> bool:
        """Delete a client by its `clientId` via the Admin API -- the
        inverse of `register_client`/`register_public_client`. Idempotent
        when the client doesn't exist: returns `False` rather than raising,
        so `loom idp unregister` stays safe to re-run (same guarantee
        `_raise_unless_already_exists` gives the create path, just for
        "already gone" instead of "already exists"). Returns whether a
        client was actually deleted."""
        headers = {'Authorization': f'Bearer {self._token}'}
        async with self._client() as http:
            internal_ref = await self._find_client_ref(http, client_id, headers)
            if internal_ref is None:
                return False
            response = await http.delete(
                f'{self._realm_admin_base}/clients/{internal_ref}',
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
        return True

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
