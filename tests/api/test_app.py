import pytest


@pytest.mark.asyncio
async def test_healthz_is_unauthenticated(api_client):
    response = await api_client.get('/healthz')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
