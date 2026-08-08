from loom.idp.catalog_roles import ROLE_BUNDLES, content_scopes, platform_scopes


def test_content_scopes_has_18_entries():
    scopes = content_scopes()
    assert len(scopes) == 18
    assert 'catalog:capability:read' in scopes
    assert 'catalog:agent:transition' in scopes


def test_platform_scopes_has_6_entries():
    scopes = platform_scopes()
    assert len(scopes) == 6
    assert 'catalog:tenant:write' in scopes
    assert 'catalog:environment:read' in scopes


def test_role_bundles_are_correctly_layered():
    assert ROLE_BUNDLES['catalog-viewer'] < ROLE_BUNDLES['catalog-editor']
    assert ROLE_BUNDLES['catalog-editor'] <= ROLE_BUNDLES['catalog-admin']
    assert ROLE_BUNDLES['catalog-approver'] <= ROLE_BUNDLES['catalog-admin']
    assert ROLE_BUNDLES['catalog-admin'] == content_scopes()
    assert (
        ROLE_BUNDLES['catalog-platform-admin'] == content_scopes() | platform_scopes()
    )
