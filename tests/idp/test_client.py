from loom.idp.client import (
    ClientRegistrationResult,
    IdpAdminClient,
    catalog_role_definitions,
)


def test_catalog_role_definitions_has_29_entries():
    roles = catalog_role_definitions()
    assert len(roles) == 29
    leaf = [r for r in roles if not r.composite_of]
    composite = [r for r in roles if r.composite_of]
    assert len(leaf) == 24
    assert len(composite) == 5


def test_composite_roles_only_reference_known_leaf_names():
    roles = catalog_role_definitions()
    leaf_names = {r.name for r in roles if not r.composite_of}
    for role in roles:
        if role.composite_of:
            assert set(role.composite_of) <= leaf_names


class _FakeIdpAdminClient:
    async def register_client(self, *, client_id, client_name, service_account):
        del client_id, client_name, service_account
        return ClientRegistrationResult(
            client_id='test-id',
            internal_ref='test-ref',
            registration_access_token=None,
        )

    async def declare_client_roles(self, client_ref, roles):
        del client_ref, roles


def test_fake_client_satisfies_the_protocol():
    client: IdpAdminClient = _FakeIdpAdminClient()
    assert isinstance(client, _FakeIdpAdminClient)
