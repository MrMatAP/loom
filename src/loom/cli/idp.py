import argparse

from loom.config import RootConfig
from loom.idp.client import catalog_role_definitions
from loom.idp.keycloak import KeycloakAdminClient


async def idp_register_client(config: RootConfig, args: argparse.Namespace) -> int:
    """Register the Catalog OAuth client and declare its role vocabulary."""
    issuer_url = args.issuer_url or config.auth.issuer
    if not issuer_url:
        print('No --issuer-url given and config.auth.issuer is unset.')
        return 1

    client_name = args.client_name or args.client_id
    client = KeycloakAdminClient(issuer=issuer_url, token=args.token)
    result = await client.register_client(
        client_id=args.client_id, client_name=client_name, service_account=True
    )

    roles = catalog_role_definitions()
    await client.declare_client_roles(result.internal_ref, roles)

    print(
        f'Registered client: {result.client_id} (internal ref: {result.internal_ref})'
    )
    if result.registration_access_token:
        print('Registration access token (store securely, shown once):')
        print(result.registration_access_token)
    print(f'Declared {len(roles)} roles under the client.')
    return 0
