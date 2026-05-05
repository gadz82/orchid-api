"""Tests for the mini-agent SSE event surface.

Drives :func:`stream_supervisor_tokens` directly with a hand-crafted
async generator that yields the four ``mini_agent.*`` SystemMessages
(plus a normal supervisor synthesis token) and asserts that every
event fires in order, suppressed mini chunks don't leak, and the
shape of each SSE payload matches the streaming contract.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchid_ai.observability import OrchidMetricsHandler, make_event_message
from orchid_api.routers._helpers import PreparedState
from orchid_api.routers._streaming import stream_supervisor_tokens
from orchid_api.settings import Settings


def _parse_events(frames: list[str]) -> list[dict]:
    parsed: list[dict] = []
    for raw in frames:
        for chunk in raw.split("\n\n"):
            chunk = chunk.strip()
            if chunk.startswith("data: "):
                parsed.append(json.loads(chunk[len("data: ") :]))
    return parsed


def _msg(content: str = "", **extra) -> MagicMock:
    """Mock a streamed AIMessage chunk."""
    m = MagicMock()
    m.content = content
    m.tool_calls = None
    m.additional_kwargs = extra.get("additional_kwargs", {})
    return m


class _ScriptedGraph:
    """Replays a fixed list of ``(mode, payload)`` tuples through ``astream``."""

    def __init__(self, frames: list[tuple[str, object]]):
        self._frames = frames

    async def astream(self, *_args, **_kwargs):
        for mode, payload in self._frames:
            yield mode, payload


@pytest.fixture
def chat_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.add_message = AsyncMock()
    repo.get_messages = AsyncMock(return_value=[])
    repo.get_chat = AsyncMock(return_value=None)
    return repo


def _prepared(message: str = "do A and B") -> PreparedState:
    return PreparedState(
        initial_state={"messages": []},
        message=message,
        history_rows=[],
        mcp_auth_status={},
    )


@pytest.mark.asyncio
async def test_all_four_events_fire_in_order(chat_repo):
    """T18 — drive the four mini-agent events through the streaming router."""

    decomposed = make_event_message(
        "mini_agent.decomposed",
        {
            "parent": "support",
            "count": 2,
            "sub_tasks": [
                {"id": "mini_0", "description": "lookup user"},
                {"id": "mini_1", "description": "lookup orders"},
            ],
        },
    )
    started_0 = make_event_message(
        "mini_agent.started",
        {"parent": "support", "mini_id": "mini_0", "description": "lookup user"},
    )
    finished_0 = make_event_message(
        "mini_agent.finished",
        {"parent": "support", "mini_id": "mini_0", "status": "ok", "duration_ms": 12},
    )
    started_1 = make_event_message(
        "mini_agent.started",
        {"parent": "support", "mini_id": "mini_1", "description": "lookup orders"},
    )
    finished_1 = make_event_message(
        "mini_agent.finished",
        {"parent": "support", "mini_id": "mini_1", "status": "ok", "duration_ms": 18},
    )
    aggregated = make_event_message(
        "mini_agent.aggregated",
        {
            "parent": "support",
            "outcomes": [
                {"mini_id": "mini_0", "status": "ok"},
                {"mini_id": "mini_1", "status": "ok"},
            ],
        },
    )

    # The graph yields events from various nodes (parent/mini/aggregator)
    # plus a real synthesis token from the supervisor.  An ``_agent``-
    # scoped AIMessage triggers the existing ``status: started`` flow
    # so we know the regular path still fires.
    parent_meta = {"langgraph_node": "support_agent"}
    mini_meta = {"langgraph_node": "support_mini"}
    aggregator_meta = {"langgraph_node": "support_aggregator"}
    supervisor_meta = {"langgraph_node": "supervisor"}

    frames: list[tuple[str, object]] = [
        # Parent node fires the decomposed event.
        ("messages", (decomposed, parent_meta)),
        # First mini emits started + finished (single state delta).
        ("messages", (started_0, mini_meta)),
        ("messages", (finished_0, mini_meta)),
        # Internal mini token chunk — MUST be suppressed.
        ("messages", (_msg("internal-mini-token-leaked"), mini_meta)),
        # Second mini.
        ("messages", (started_1, mini_meta)),
        ("messages", (finished_1, mini_meta)),
        # Aggregator emits its event before the AIMessage.
        ("messages", (aggregated, aggregator_meta)),
        # Aggregator's AIMessage — ALSO suppressed via the *_aggregator filter.
        ("messages", (_msg("[Support Agent]\nfound user X with 3 orders"), aggregator_meta)),
        # Real supervisor synthesis token.
        ("messages", (_msg("Here is the synthesis. " * 5), supervisor_meta)),
        # values stream — graph is done.
        ("values", {"final_response": "Here is the synthesis. " * 5}),
    ]

    response = stream_supervisor_tokens(
        graph=_ScriptedGraph(frames),
        prepared=_prepared(),
        chat_id="chat-1",
        request_id="req-1",
        request_start=0.0,
        settings=Settings(stream_max_seconds=30),
        chat_repo=chat_repo,
        metrics=OrchidMetricsHandler(),
    )

    sse_frames: list[str] = []
    async for chunk in response:
        sse_frames.append(chunk)
    events = _parse_events(sse_frames)

    types_in_order = [e["type"] for e in events]
    # All four mini-agent event types fire, in order, with the right
    # sequencing relative to each other.
    assert "mini_agent.decomposed" in types_in_order
    assert types_in_order.count("mini_agent.started") == 2
    assert types_in_order.count("mini_agent.finished") == 2
    assert types_in_order.count("mini_agent.aggregated") == 1

    decomposed_idx = types_in_order.index("mini_agent.decomposed")
    aggregated_idx = types_in_order.index("mini_agent.aggregated")
    started_indices = [i for i, t in enumerate(types_in_order) if t == "mini_agent.started"]
    finished_indices = [i for i, t in enumerate(types_in_order) if t == "mini_agent.finished"]

    # decomposed ─ first; aggregated ─ last (among mini_agent events).
    assert all(decomposed_idx < i for i in started_indices + finished_indices)
    assert all(aggregated_idx > i for i in started_indices + finished_indices)
    # Each pair: started precedes its finished.
    assert started_indices[0] < finished_indices[0]
    assert started_indices[1] < finished_indices[1]

    # Internal mini token leak suppression.
    for ev in events:
        assert "internal-mini-token-leaked" not in (ev.get("content") or "")

    # Aggregator's AIMessage tokens are also suppressed via the
    # ``*_aggregator`` filter — the only token in the SSE stream
    # comes from the supervisor.
    token_events = [e for e in events if e.get("type") == "token"]
    assert token_events
    assert all("[Support Agent]" not in (e.get("content") or "") for e in token_events)

    # Payload shape — ``mini_agent.decomposed``.
    decomposed_event = next(e for e in events if e["type"] == "mini_agent.decomposed")
    assert decomposed_event["parent"] == "support"
    assert decomposed_event["count"] == 2
    assert {st["id"] for st in decomposed_event["sub_tasks"]} == {"mini_0", "mini_1"}

    # Payload shape — ``mini_agent.finished`` carries ``status`` + ``duration_ms``.
    finished_payloads = [e for e in events if e["type"] == "mini_agent.finished"]
    assert {p["status"] for p in finished_payloads} == {"ok"}
    assert all("duration_ms" in p for p in finished_payloads)


@pytest.mark.asyncio
async def test_finished_event_with_error_propagates(chat_repo):
    """``mini_agent.finished`` keeps the optional ``error`` field intact."""
    finished = make_event_message(
        "mini_agent.finished",
        {
            "parent": "support",
            "mini_id": "mini_boom",
            "status": "failed",
            "duration_ms": 7,
            "error": "RuntimeError: kaboom",
        },
    )
    frames = [
        ("messages", (finished, {"langgraph_node": "support_mini"})),
        ("values", {"final_response": "x"}),
    ]
    response = stream_supervisor_tokens(
        graph=_ScriptedGraph(frames),
        prepared=_prepared(),
        chat_id="chat-1",
        request_id="req-1",
        request_start=0.0,
        settings=Settings(stream_max_seconds=30),
        chat_repo=chat_repo,
        metrics=OrchidMetricsHandler(),
    )
    sse_frames: list[str] = []
    async for chunk in response:
        sse_frames.append(chunk)
    events = _parse_events(sse_frames)
    finished_event = next(e for e in events if e["type"] == "mini_agent.finished")
    assert finished_event["error"] == "RuntimeError: kaboom"
    assert finished_event["status"] == "failed"
