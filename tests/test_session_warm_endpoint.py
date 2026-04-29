"""Tests for ``POST /session/warm`` per-user warm-up endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.mcp.session_warmer import OrchidWarmReport

from orchid_api.routers.session import warm_session


def _auth() -> OrchidAuthContext:
    return OrchidAuthContext(access_token="t", tenant_key="t1", user_id="u1")


def _fake_orchid(report: OrchidWarmReport) -> MagicMock:
    fake = MagicMock()
    fake.session_warmer = MagicMock()
    fake.session_warmer.warm_for_user = AsyncMock(return_value=report)
    return fake


@pytest.mark.asyncio
async def test_warm_session_returns_report():
    report = OrchidWarmReport(
        warmed=["internal-rest"],
        skipped=["ext-crm"],
        failed={},
    )
    fake_orchid = _fake_orchid(report)

    with patch("orchid_api.routers.session.app_ctx") as mock_ctx:
        mock_ctx.orchid = fake_orchid
        result = await warm_session(auth=_auth())

    assert result == {
        "warmed": ["internal-rest"],
        "skipped": ["ext-crm"],
        "failed": {},
    }
    fake_orchid.session_warmer.warm_for_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_warm_session_idempotent_returns_empty_report():
    """Second call by the same user yields an empty-but-OK report."""
    fake_orchid = _fake_orchid(OrchidWarmReport())

    with patch("orchid_api.routers.session.app_ctx") as mock_ctx:
        mock_ctx.orchid = fake_orchid
        result = await warm_session(auth=_auth())

    assert result == {"warmed": [], "skipped": [], "failed": {}}


@pytest.mark.asyncio
async def test_warm_session_raises_503_when_runtime_missing():
    with patch("orchid_api.routers.session.app_ctx") as mock_ctx:
        mock_ctx.orchid = None
        with pytest.raises(HTTPException) as exc_info:
            await warm_session(auth=_auth())
    assert exc_info.value.status_code == 503
