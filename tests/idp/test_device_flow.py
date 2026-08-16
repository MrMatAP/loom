import httpx
import pytest

from loom.idp.device_flow import (
    DeviceAuthorization,
    DeviceCodeClient,
    DeviceCodeError,
    DeviceFlowEndpoints,
)

_ENDPOINTS = DeviceFlowEndpoints(
    device_authorization_endpoint=(
        'https://idp.example/realms/loom/protocol/openid-connect/auth/device'
    ),
    token_endpoint='https://idp.example/realms/loom/protocol/openid-connect/token',
)


async def _no_sleep(seconds: float) -> None:
    del seconds


@pytest.mark.asyncio
async def test_start_posts_device_authorization_request():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/realms/loom/protocol/openid-connect/auth/device'
        body = dict(httpx.QueryParams(request.read().decode()))
        assert body['client_id'] == 'loom-cli'
        return httpx.Response(
            200,
            json={
                'device_code': 'devcode-123',
                'user_code': 'ABCD-EFGH',
                'verification_uri': 'https://idp.example/device',
                'verification_uri_complete': (
                    'https://idp.example/device?user_code=ABCD-EFGH'
                ),
                'expires_in': 600,
                'interval': 5,
            },
        )

    client = DeviceCodeClient(
        _ENDPOINTS,
        'loom-cli',
        transport=httpx.MockTransport(handler),
    )

    authorization = await client.start()
    assert authorization.device_code == 'devcode-123'
    assert authorization.user_code == 'ABCD-EFGH'
    assert authorization.verification_uri_complete == (
        'https://idp.example/device?user_code=ABCD-EFGH'
    )
    assert authorization.expires_in == 600
    assert authorization.interval == 5


@pytest.mark.asyncio
async def test_start_defaults_interval_when_absent():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                'device_code': 'devcode-123',
                'user_code': 'ABCD-EFGH',
                'verification_uri': 'https://idp.example/device',
                'expires_in': 600,
            },
        )

    client = DeviceCodeClient(
        _ENDPOINTS,
        'loom-cli',
        transport=httpx.MockTransport(handler),
    )

    authorization = await client.start()
    assert authorization.interval == 5
    assert authorization.verification_uri_complete is None


@pytest.mark.asyncio
async def test_poll_returns_tokens_once_authorized():
    attempts = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/realms/loom/protocol/openid-connect/token'
        body = dict(httpx.QueryParams(request.read().decode()))
        assert body['grant_type'] == 'urn:ietf:params:oauth:grant-type:device_code'
        assert body['device_code'] == 'devcode-123'
        assert body['client_id'] == 'loom-cli'
        attempts['count'] += 1
        if attempts['count'] < 3:
            return httpx.Response(400, json={'error': 'authorization_pending'})
        return httpx.Response(
            200,
            json={
                'access_token': 'access-tok',
                'refresh_token': 'refresh-tok',
                'expires_in': 300,
            },
        )

    client = DeviceCodeClient(
        _ENDPOINTS,
        'loom-cli',
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
    )
    authorization = DeviceAuthorization(
        device_code='devcode-123',
        user_code='ABCD-EFGH',
        verification_uri='https://idp.example/device',
        verification_uri_complete=None,
        expires_in=600,
        interval=5,
    )

    tokens = await client.poll(authorization)
    assert tokens.access_token == 'access-tok'
    assert tokens.refresh_token == 'refresh-tok'
    assert tokens.expires_in == 300
    assert attempts['count'] == 3


@pytest.mark.asyncio
async def test_poll_honors_slow_down_and_still_succeeds():
    attempts = {'count': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        attempts['count'] += 1
        if attempts['count'] == 1:
            return httpx.Response(400, json={'error': 'slow_down'})
        return httpx.Response(
            200, json={'access_token': 'a', 'refresh_token': None, 'expires_in': 60}
        )

    client = DeviceCodeClient(
        _ENDPOINTS,
        'loom-cli',
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
    )
    authorization = DeviceAuthorization(
        device_code='devcode-123',
        user_code='ABCD-EFGH',
        verification_uri='https://idp.example/device',
        verification_uri_complete=None,
        expires_in=600,
        interval=5,
    )

    tokens = await client.poll(authorization)
    assert tokens.access_token == 'a'
    assert tokens.refresh_token is None


@pytest.mark.asyncio
async def test_poll_raises_on_access_denied():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, json={'error': 'access_denied'})

    client = DeviceCodeClient(
        _ENDPOINTS,
        'loom-cli',
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
    )
    authorization = DeviceAuthorization(
        device_code='devcode-123',
        user_code='ABCD-EFGH',
        verification_uri='https://idp.example/device',
        verification_uri_complete=None,
        expires_in=600,
        interval=5,
    )

    with pytest.raises(DeviceCodeError) as exc_info:
        await client.poll(authorization)
    assert 'access_denied' in str(exc_info.value)


@pytest.mark.asyncio
async def test_poll_raises_when_never_authorized_before_expiry():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, json={'error': 'authorization_pending'})

    client = DeviceCodeClient(
        _ENDPOINTS,
        'loom-cli',
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
    )
    authorization = DeviceAuthorization(
        device_code='devcode-123',
        user_code='ABCD-EFGH',
        verification_uri='https://idp.example/device',
        verification_uri_complete=None,
        expires_in=15,
        interval=5,
    )

    with pytest.raises(DeviceCodeError) as exc_info:
        await client.poll(authorization)
    assert 'timed out' in str(exc_info.value)
