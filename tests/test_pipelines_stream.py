"""HTTP pipeline streaming: SSE order, terminal result and disconnect cleanup."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from orchid_ai.config.schema_pipeline import OrchidPipelinesConfig
from orchid_ai.core.pipeline import OrchidPipelineEvent, OrchidPipelineResult
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.pipeline.registry import PipelineRegistry

from orchid_api.routers import pipelines as pipelines_router
from orchid_api.routers.pipelines import PipelineAPIError, pipeline_error_handler


@pytest.fixture
def stream_steps(monkeypatch: pytest.MonkeyPatch) -> str:
    module = types.ModuleType("stream_steps")

    def load(ctx):
        return {"ok": True}

    module.load = load  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "stream_steps", module)
    return "stream_steps"


def _registry() -> PipelineRegistry:
    config = OrchidPipelinesConfig.model_validate(
        {
            "enabled": True,
            "definitions": {
                "demo": {
                    "description": "Demo pipeline",
                    "input": {"fields": {"account_id": {"type": "string", "required": True}}},
                    "jobs": {"load": {"kind": "deterministic", "uses": "stream_steps.load"}},
                    "expose": {"as_tool": True, "description": "Run demo"},
                }
            },
        }
    )
    return PipelineRegistry.from_config(config)


def _auth() -> OrchidAuthContext:
    return OrchidAuthContext(access_token="t", tenant_key="t1", user_id="u1")


def _event(type: str, *, data: dict[str, Any] | None = None) -> OrchidPipelineEvent:
    return OrchidPipelineEvent(
        type=type,
        run_id="run-1",
        timestamp=datetime.now(UTC),
        data=data or {},
    )


class _Stream:
    def __init__(self, events: list[OrchidPipelineEvent], *, delay: float = 0.0) -> None:
        self.events = events
        self.delay = delay
        self.index = 0
        self.closed = False
        self.cancelled = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.index >= len(self.events):
            raise StopAsyncIteration
        event = self.events[self.index]
        self.index += 1
        return event

    async def aclose(self) -> None:
        self.closed = True


class _StreamingOrchid:
    def __init__(self, registry: Any, events: list[OrchidPipelineEvent]) -> None:
        self.pipelines = registry
        self.config = types.SimpleNamespace(events=None)
        self.events = events
        self.calls: list[dict[str, Any]] = []

    def stream_pipeline(self, name, input, **kwargs):
        self.calls.append({"name": name, "input": input, **kwargs})
        return _Stream(self.events)


def _result() -> OrchidPipelineResult:
    now = datetime.now(UTC)
    return OrchidPipelineResult(
        run_id="run-1",
        pipeline="demo",
        status="succeeded",
        summary="ok",
        output={"ok": True},
        graph_stats={"objects": 1},
        started_at=now,
        finished_at=now,
    )


def _app(
    *,
    registry: Any,
    orchid: Any,
    auth: bool = True,
) -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(PipelineAPIError, pipeline_error_handler)
    app.include_router(pipelines_router.router)
    if auth:
        app.dependency_overrides[pipelines_router.require_pipeline_auth] = lambda: _auth()
    app.dependency_overrides[pipelines_router.get_pipelines] = lambda: registry
    app.dependency_overrides[pipelines_router._get_orchid] = lambda: orchid
    return app


def test_stream_emits_ordered_frames_and_terminal_result(stream_steps: str) -> None:
    registry = _registry()
    result = _result()
    events = [
        _event("pipeline.started", data={"pipeline": "demo"}),
        _event("pipeline.job.started", data={"job": "load", "kind": "deterministic"}),
        _event("pipeline.finished", data={"status": "succeeded", "result": result.to_dict()}),
    ]
    orchid = _StreamingOrchid(registry, events)
    with (
        TestClient(_app(registry=registry, orchid=orchid)) as client,
        client.stream(
            "POST",
            "/pipelines/demo/runs/stream",
            json={"input": {"account_id": "a1"}},
        ) as response,
    ):
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        frames = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]

    assert [frame["type"] for frame in frames] == [
        "pipeline.started",
        "pipeline.job.started",
        "pipeline.finished",
    ]
    assert frames[-1]["data"]["result"]["status"] == "succeeded"
    [call] = orchid.calls
    assert call["mode"] == "direct"
    assert call["seed"] == []


def test_stream_rejects_async_mode(stream_steps: str) -> None:
    registry = _registry()
    orchid = _StreamingOrchid(registry, [])
    with TestClient(_app(registry=registry, orchid=orchid)) as client:
        response = client.post(
            "/pipelines/demo/runs/stream",
            json={"input": {"account_id": "a1"}, "mode": "async"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "pipeline_input_invalid"
    assert orchid.calls == []


def test_stream_requires_auth(stream_steps: str) -> None:
    registry = _registry()
    orchid = _StreamingOrchid(registry, [])
    with TestClient(_app(registry=registry, orchid=orchid, auth=False)) as client:
        response = client.post(
            "/pipelines/demo/runs/stream",
            json={"input": {"account_id": "a1"}},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "pipeline_unauthorized"


def test_stream_input_is_validated_before_a_run(stream_steps: str) -> None:
    registry = _registry()
    orchid = _StreamingOrchid(registry, [])
    with TestClient(_app(registry=registry, orchid=orchid)) as client:
        response = client.post("/pipelines/demo/runs/stream", json={"input": {}})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "pipeline_input_schema_invalid"
    assert orchid.calls == []


async def test_stream_frames_cancel_and_close_on_disconnect(stream_steps: str) -> None:
    iterator = _Stream([_event("pipeline.started"), _event("pipeline.finished")], delay=0.05)

    class _Request:
        async def is_disconnected(self) -> bool:
            return False

    frames = pipelines_router._stream_frames(iterator, _Request(), "demo")
    first = await anext(frames)
    assert first.startswith("data: ")
    # Resume the generator so it creates the next pull task, then cancel the
    # consumer; the generator must cancel that pull and close the iterator.
    resume = asyncio.create_task(anext(frames))
    await asyncio.sleep(0.01)
    resume.cancel()
    with pytest.raises(asyncio.CancelledError):
        await resume
    await frames.aclose()

    assert iterator.closed is True
    assert iterator.cancelled is True
