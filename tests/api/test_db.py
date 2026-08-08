import pytest

from loom.api.catalog.db import flush_or_raise
from loom.api.catalog.exceptions import DomainValidationError
from loom.model.tenant import Tenant


@pytest.mark.asyncio
async def test_flush_or_raise_translates_integrity_error(async_session):
    async_session.add(Tenant(slug='dup', name='A'))
    await flush_or_raise(async_session)
    async_session.add(Tenant(slug='dup', name='B'))
    with pytest.raises(DomainValidationError):
        await flush_or_raise(async_session)
