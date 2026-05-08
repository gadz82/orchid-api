"""Lifecycle tests: DevBypassIdentityResolver wiring and _build_graph_invoker.

Covers the identity-resolver selection logic in ``setup_orchid`` under
three branches:
  - ``identity_resolver_class`` set → real resolver
  - ``dev_auth_bypass=True`` and no class → DevBypassIdentityResolver
  - neither → None

Also verifies that ``_build_graph_invoker`` returns a coroutine function
that calls the compiled graph and returns only ``final_response``.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchid_ai.mcp.session_warmer import OrchidWarmReport

from orchid_api import lifecycle as lifecycle_module
from orchid_api.context import app_ctx
from orchid_api.dev_identity import DevBypassIdentityResolver
from orchid_api.settings import Settings


# ── Shared reset fixture ─────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_app_ctx():
    saved_orchid = app_ctx.orchid
    saved_http = app_ctx.http_client
    saved_resolver = app_ctx.identity_resolver
    saved_oauth = app_ctx.oauth_state_store
    saved_events = app_ctx.events
    yield
    app_ctx.orchid = saved_orchid
    app_ctx.http_client = saved_http
    app_ctx.identity_resolver = saved_resolver
    app_ctx.oauth_state_store = saved_oauth
    app_ctx.events = saved_events


def _fake_orchid() -> MagicMock:
    fake = MagicMock()
    fake.warm_unauthenticated_capabilities = AsyncMock(return_value=OrchidWarmReport(warmed=[], skipped=[], failed={}))
    fake.mcp_token_store = None
    fake.config = MagicMock()
    fake.config.agents = {}
    fake.config.events = None
    fake.session_warmer = MagicMock()
    fake.graph = MagicMock()
    return fake


def _base_patches(fake_orchid):
    """Return a list of patch context managers for ``setup_orchid`` scaffolding."""
    return [
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
    ]


# ── Identity-resolver selection ──────────────────────────────


@pytest.mark.asyncio
async def test_dev_auth_bypass_wires_dev_bypass_resolver(caplog):
    """dev_auth_bypass=True + no resolver class → DevBypassIdentityResolver."""
    fake = _fake_orchid()
    with ExitStack() as stack:
        for p in _base_patches(fake):
            stack.enter_context(p)
        stack.enter_context(caplog.at_level(logging.WARNING, logger="orchid_api.lifecycle"))
        await lifecycle_module.setup_orchid(settings=Settings(dev_auth_bypass=True))

    assert isinstance(app_ctx.identity_resolver, DevBypassIdentityResolver)
    assert any("DevBypassIdentityResolver" in m for m in caplog.messages)


@pytest.mark.asyncio
async def test_no_bypass_no_class_leaves_resolver_none():
    """Neither dev_auth_bypass nor identity_resolver_class → resolver is None."""
    fake = _fake_orchid()
    with ExitStack() as stack:
        for p in _base_patches(fake):
            stack.enter_context(p)
        await lifecycle_module.setup_orchid(settings=Settings(dev_auth_bypass=False))

    assert app_ctx.identity_resolver is None


@pytest.mark.asyncio
async def test_identity_resolver_class_takes_priority_over_dev_bypass():
    """identity_resolver_class is set → that class is used, not DevBypass."""
    fake = _fake_orchid()

    class _StubResolver:
        def __init__(self, *, http_client):
            pass

    with ExitStack() as stack:
        for p in _base_patches(fake):
            stack.enter_context(p)
        stack.enter_context(patch.object(lifecycle_module, "import_class", return_value=_StubResolver))
        await lifecycle_module.setup_orchid(
            settings=Settings(
                dev_auth_bypass=True,
                identity_resolver_class="some.module.StubResolver",
            )
        )

    assert isinstance(app_ctx.identity_resolver, _StubResolver)


# ── _build_graph_invoker ─────────────────────────────────────


@pytest.mark.asyncio
async def test_build_graph_invoker_returns_final_response_only():
    """The invoker closure must return only final_response (not raw graph state)."""
    fake = _fake_orchid()
    fake.graph.ainvoke = AsyncMock(
        return_value={
            "final_response": "All good",
            "messages": object(),  # non-serializable — must be stripped
        }
    )
    app_ctx.orchid = fake

    invoker = lifecycle_module._build_graph_invoker()

    run = MagicMock()
    run.run_id = "run-123"
    run.spec.prompt = "Hello"
    auth = MagicMock()

    result = await invoker(run, auth)

    assert result == {"final_response": "All good"}
    fake.graph.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_graph_invoker_falls_back_to_empty_string_when_no_final_response():
    """When the graph returns no final_response key, invoker returns empty string."""
    fake = _fake_orchid()
    fake.graph.ainvoke = AsyncMock(return_value={"messages": []})
    app_ctx.orchid = fake

    invoker = lifecycle_module._build_graph_invoker()

    run = MagicMock()
    run.run_id = "run-456"
    run.spec.prompt = "?"
    auth = MagicMock()

    result = await invoker(run, auth)
    assert result == {"final_response": ""}
