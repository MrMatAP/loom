import pytest
import sqlalchemy as sa

from loom.api.catalog.dependencies import get_session
from loom.model.tenant import Tenant


@pytest.mark.asyncio
async def test_healthz_is_unauthenticated(api_client):
    response = await api_client.get('/healthz')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


@pytest.mark.asyncio
async def test_api_client_session_override_commits(api_client, async_session_factory):
    """A write through api_client's overridden get_session is really committed."""
    session_override = api_client.app.dependency_overrides[get_session]
    generator = session_override()
    session = await anext(generator)
    session.add(Tenant(slug='commit-check', name='Commit Check'))
    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    async with async_session_factory() as verify_session:
        result = await verify_session.execute(
            sa.select(Tenant).where(Tenant.slug == 'commit-check')
        )
        assert result.scalar_one_or_none() is not None
