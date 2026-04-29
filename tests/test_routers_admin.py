"""Tests for ``orchid_api.routers.admin`` — bulk indexing endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from orchid_ai.core.state import OrchidAuthContext

from orchid_api.models import IndexRequest
from orchid_api.routers.admin import index_data
from orchid_api.settings import Settings


@pytest.fixture
def auth():
    return OrchidAuthContext(access_token="tok", tenant_key="t1", user_id="u1")


def _runtime(reader) -> MagicMock:
    rt = MagicMock()
    rt.get_reader.return_value = reader
    return rt


@pytest.mark.asyncio
async def test_index_disabled_by_default(auth):
    """The /index endpoint is disabled by default (allow_index_endpoint=False)."""
    with pytest.raises(HTTPException) as exc:
        await index_data(IndexRequest(), auth=auth, settings=Settings(), runtime=_runtime(object()))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_index_no_writer(auth):
    """When enabled but reader isn't a OrchidVectorWriter, index_data should 503."""
    settings = Settings(allow_index_endpoint=True)
    with pytest.raises(HTTPException) as exc:
        await index_data(IndexRequest(), auth=auth, settings=settings, runtime=_runtime(object()))
    assert exc.value.status_code == 503
