"""Lifecycle integration: assert MCP capability warm-up at startup.

When ``setup_orchid()`` runs, the framework's
:meth:`Orchid.warm_unauthenticated_capabilities` must be awaited so
``auth.mode: none`` MCP servers populate their capability caches
before the first chat request.  Failures in the warm-up MUST NOT
abort startup — they get logged and shrugged off.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchid_ai.mcp.session_warmer import OrchidWarmReport

from orchid_api import lifecycle as lifecycle_module
from orchid_api.context import app_ctx
from orchid_api.settings import Settings


@pytest.fixture(autouse=True)
def _reset_app_ctx():
    """Each test gets a clean ``app_ctx`` so we don't leak state."""
    saved_orchid = app_ctx.orchid
    saved_http = app_ctx.http_client
    saved_resolver = app_ctx.identity_resolver
    saved_oauth_state = app_ctx.oauth_state_store
    yield
    app_ctx.orchid = saved_orchid
    app_ctx.http_client = saved_http
    app_ctx.identity_resolver = saved_resolver
    app_ctx.oauth_state_store = saved_oauth_state


def _build_fake_orchid(report: OrchidWarmReport) -> MagicMock:
    fake = MagicMock()
    fake.warm_unauthenticated_capabilities = AsyncMock(return_value=report)
    fake.config = MagicMock()
    fake.config.agents = {"alpha": MagicMock()}
    fake.session_warmer = MagicMock()
    return fake


@pytest.mark.asyncio
async def test_setup_orchid_invokes_unauthenticated_warm():
    fake_report = OrchidWarmReport(warmed=["local-tool"], skipped=[], failed={})
    fake_orchid = _build_fake_orchid(fake_report)

    with (
        patch.object(lifecycle_module, "configure_tracing"),
        patch.object(
            lifecycle_module.Orchid,
            "from_config_path",
            new=AsyncMock(return_value=fake_orchid),
        ),
        patch.object(
            lifecycle_module,
            "build_oauth_state_store",
            new=AsyncMock(return_value=MagicMock()),
        ),
    ):
        # Settings without OAuth/identity wiring — exercises the bare
        # path through ``setup_orchid``.
        settings = Settings(dev_auth_bypass=True)
        await lifecycle_module.setup_orchid(settings=settings)

    fake_orchid.warm_unauthenticated_capabilities.assert_awaited_once()


@pytest.mark.asyncio
async def test_setup_orchid_swallows_warm_failures(caplog):
    fake_orchid = MagicMock()
    fake_orchid.warm_unauthenticated_capabilities = AsyncMock(side_effect=RuntimeError("upstream MCP unreachable"))
    fake_orchid.config = MagicMock()
    fake_orchid.config.agents = {}
    fake_orchid.session_warmer = MagicMock()

    with (
        patch.object(lifecycle_module, "configure_tracing"),
        patch.object(
            lifecycle_module.Orchid,
            "from_config_path",
            new=AsyncMock(return_value=fake_orchid),
        ),
        patch.object(
            lifecycle_module,
            "build_oauth_state_store",
            new=AsyncMock(return_value=MagicMock()),
        ),
        caplog.at_level(logging.WARNING, logger="orchid_api.lifecycle"),
    ):
        settings = Settings(dev_auth_bypass=True)
        # Must NOT raise — startup is not allowed to abort on warm-up.
        await lifecycle_module.setup_orchid(settings=settings)

    fake_orchid.warm_unauthenticated_capabilities.assert_awaited_once()
    assert any("MCP startup warm-up raised" in m for m in caplog.messages)
