"""Tests for ``orchid_api.routers.streaming`` — focus on the timeout +
cancellation hardening of the SSE endpoint.

The endpoint's ``event_generator`` is a closure inside
``stream_chat_message``; we exercise it indirectly by mocking the graph
so it yields very slowly, paired with a tiny ``stream_max_seconds``
budget. The streaming response is consumed via ``httpx`` in a real
event loop so cancellation propagates the same way Starlette does in
production.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchid_ai.core.state import OrchidAuthContext

from orchid_api.routers import streaming as streaming_router
from orchid_api.routers.streaming import stream_chat_message
from orchid_api.settings import Settings


@pytest.fixture
def auth():
    return OrchidAuthContext(access_token="t", tenant_key="t1", user_id="u1")


def _runtime():
    rt = MagicMock()
    rt.get_reader.return_value = MagicMock()
    rt.mcp_auth_registry = None
    rt.checkpointer = None
    return rt


async def _drain(streaming_response):
    """Consume a ``StreamingResponse`` body and split on SSE frames."""
    chunks: list[str] = []
    async for raw in streaming_response.body_iterator:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        chunks.append(raw)
    body = "".join(chunks)
    events = []
    for frame in body.split("\n\n"):
        frame = frame.strip()
        if frame.startswith("data: "):
            events.append(json.loads(frame[len("data: ") :]))
    return events


class _SlowGraph:
    """Mock LangGraph whose ``astream`` yields token frames very slowly."""

    def __init__(self, sleep_per_event: float):
        self._sleep = sleep_per_event
        self.cancelled = False

    async def astream(self, *_args, **_kwargs):
        try:
            for _ in range(50):
                await asyncio.sleep(self._sleep)
                # Shape mirrors what the real graph yields under stream_mode=["messages","values"]
                msg = MagicMock()
                msg.content = "hello"
                msg.tool_calls = None
                yield "messages", (msg, {"langgraph_node": "supervisor"})
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.asyncio
async def test_stream_emits_timeout_event_when_budget_exceeded(auth, monkeypatch, sample_session):
    """When the graph runs longer than ``stream_max_seconds`` the generator
    yields an explicit error frame plus a ``done`` frame with ``timed_out=True``."""
    chat_repo = AsyncMock()
    chat_repo.get_chat = AsyncMock(return_value=sample_session)
    chat_repo.get_messages = AsyncMock(return_value=[])

    settings = Settings(stream_max_seconds=1)
    graph = _SlowGraph(sleep_per_event=10.0)

    # ``prepare_graph_state`` does heavy work (file parsing, identity, …);
    # short-circuit it for this test.
    async def _fake_prepare(*_a, **_kw):
        from orchid_api.routers._helpers import PreparedState

        return PreparedState(
            initial_state={},
            message="hello",
            history_rows=[],
            mcp_auth_status={},
        )

    monkeypatch.setattr(streaming_router, "prepare_graph_state", _fake_prepare)

    response = await stream_chat_message(
        "chat-001",
        message="hello",
        files=[],
        auth=auth,
        settings=settings,
        chat_repo=chat_repo,
        runtime=_runtime(),
        graph=graph,
        mcp_token_store=None,
    )

    events = await asyncio.wait_for(_drain(response), timeout=10)
    kinds = [e.get("type") for e in events]

    assert "error" in kinds, f"expected timeout error event, got {kinds}"
    assert any("budget" in (e.get("message") or "") for e in events if e.get("type") == "error")

    done_events = [e for e in events if e.get("type") == "done"]
    assert done_events, "stream must emit a final done event even on timeout"
    assert done_events[-1]["timed_out"] is True


@pytest.mark.asyncio
async def test_stream_done_event_carries_timed_out_false_on_normal_run(auth, monkeypatch, sample_session):
    """Happy-path ``done`` event reports ``timed_out=False``."""
    chat_repo = AsyncMock()
    chat_repo.get_chat = AsyncMock(return_value=sample_session)
    chat_repo.get_messages = AsyncMock(return_value=[])

    class _FastGraph:
        async def astream(self, *_, **__):
            return
            yield  # pragma: no cover — make this an async generator

    settings = Settings(stream_max_seconds=30)

    async def _fake_prepare(*_a, **_kw):
        from orchid_api.routers._helpers import PreparedState

        return PreparedState(
            initial_state={"final_response": "hi"},
            message="hello",
            history_rows=[],
            mcp_auth_status={},
        )

    monkeypatch.setattr(streaming_router, "prepare_graph_state", _fake_prepare)

    response = await stream_chat_message(
        "chat-001",
        message="hello",
        files=[],
        auth=auth,
        settings=settings,
        chat_repo=chat_repo,
        runtime=_runtime(),
        graph=_FastGraph(),
        mcp_token_store=None,
    )

    events = await _drain(response)
    done = [e for e in events if e.get("type") == "done"]
    assert done, f"expected done event, got {events}"
    assert done[-1]["timed_out"] is False


@pytest.mark.asyncio
async def test_stream_propagates_client_disconnect(auth, monkeypatch, sample_session):
    """A ``CancelledError`` from the consuming task must bubble up through
    the generator without producing a ``done`` event or persisting messages.
    Starlette uses this signal to detect that the client is gone."""
    chat_repo = AsyncMock()
    chat_repo.get_chat = AsyncMock(return_value=sample_session)
    chat_repo.get_messages = AsyncMock(return_value=[])

    graph = _SlowGraph(sleep_per_event=10.0)
    settings = Settings(stream_max_seconds=300)

    async def _fake_prepare(*_a, **_kw):
        from orchid_api.routers._helpers import PreparedState

        return PreparedState(
            initial_state={},
            message="hello",
            history_rows=[],
            mcp_auth_status={},
        )

    monkeypatch.setattr(streaming_router, "prepare_graph_state", _fake_prepare)

    response = await stream_chat_message(
        "chat-001",
        message="hello",
        files=[],
        auth=auth,
        settings=settings,
        chat_repo=chat_repo,
        runtime=_runtime(),
        graph=graph,
        mcp_token_store=None,
    )

    async def _consume_briefly():
        async for _ in response.body_iterator:
            await asyncio.sleep(0)
            break

    task = asyncio.create_task(_consume_briefly())
    await asyncio.sleep(0)  # let the task start
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Messages MUST NOT be persisted on disconnect — the response was incomplete.
    chat_repo.add_message.assert_not_called()
