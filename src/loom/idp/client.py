import dataclasses
from typing import Protocol

from .catalog_roles import ROLE_BUNDLES, content_scopes, platform_scopes


@dataclasses.dataclass(frozen=True)
class ClientRegistrationResult:
    """Result of registering a new OAuth client with the IDP."""

    client_id: str
    internal_ref: str
    registration_access_token: str | None
    client_secret: str | None = None


@dataclasses.dataclass(frozen=True)
class RoleDefinition:
    """One role to declare under a registered client, IDP-agnostic."""

    name: str
    description: str
    composite_of: tuple[str, ...] = ()


class IdpAdminClient(Protocol):
    """Provider-agnostic admin operations: client registration, roles."""

    async def register_client(
        self, *, client_id: str, client_name: str, service_account: bool
    ) -> ClientRegistrationResult: ...

    async def register_public_client(
        self,
        *,
        client_id: str,
        client_name: str,
        standard_flow: bool = False,
        device_flow: bool = False,
        redirect_uris: tuple[str, ...] = (),
        web_origins: tuple[str, ...] = (),
    ) -> ClientRegistrationResult: ...

    async def add_audience_mapper(
        self, client_ref: str, *, target_client_id: str
    ) -> None: ...

    async def add_client_roles_mapper(
        self, client_ref: str, *, source_client_id: str
    ) -> None: ...

    async def declare_client_roles(
        self, client_ref: str, roles: list[RoleDefinition]
    ) -> None: ...


def catalog_role_definitions() -> list[RoleDefinition]:
    """Build the 24 leaf-scope roles plus the 5 composite bundle roles."""
    leaf_roles = [
        RoleDefinition(name=scope, description=f'Grants {scope}')
        for scope in sorted(content_scopes() | platform_scopes())
    ]
    composite_roles = [
        RoleDefinition(
            name=name,
            description=f'Bundle role: {name}',
            composite_of=tuple(sorted(scopes)),
        )
        for name, scopes in ROLE_BUNDLES.items()
    ]
    return leaf_roles + composite_roles
