"""HTTP pipeline router: list, detail, validation, sync/async runs and errors."""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from orchid_ai.config.schema_pipeline import OrchidPipelinesConfig
from orchid_ai.core.pipeline import (
    OrchidPipelineResult,
    OrchidPipelineTimeoutError,
)
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.pipeline.launcher import PipelineLaunchResult
from orchid_ai.pipeline.registry import PipelineRegistry

from orchid_api import lifecycle as api_lifecycle
from orchid_api.context import app_ctx
from orchid_api.routers import pipelines as pipelines_router
from orchid_api.routers.pipelines import PipelineAPIError, pipeline_error_handler
from orchid_api.settings import Settings


@pytest.fixture
def p016_steps(monkeypatch: pytest.MonkeyPatch) -> str:
    module = types.ModuleType("p016_steps")

    def load(ctx):
        return {"ok": True}

    module.load = load  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "p016_steps", module)
    return "p016_steps"


def _pipelines(
    *,
    expose_extra: dict[str, Any] | None = None,
    include_second: bool = True,
    async_enabled: bool = True,
) -> OrchidPipelinesConfig:
    expose = {"as_tool": True, "description": "Run the demo pipeline"}
    expose.update(expose_extra or {})
    definitions: dict[str, Any] = {
        "demo": {
            "description": "Demo pipeline",
            "input": {"fields": {"account_id": {"type": "string", "required": True}}},
            "jobs": {"load": {"kind": "deterministic", "uses": "p016_steps.load"}},
            "expose": expose,
            "async": {
                "enabled": async_enabled,
                "signal": "pipeline.demo.requested",
                "trigger_id": "demo-trigger",
            },
        }
    }
    if include_second:
        definitions["second"] = {
            "description": "Second pipeline",
            "jobs": {"load": {"kind": "deterministic", "uses": "p016_steps.load"}},
        }
    return OrchidPipelinesConfig.model_validate({"enabled": True, "definitions": definitions})


def _registry(**kwargs) -> PipelineRegistry:
    return PipelineRegistry.from_config(_pipelines(**kwargs))


def _auth(*, roles: set[str] | None = None) -> OrchidAuthContext:
    return OrchidAuthContext(
        access_token="test-token",
        tenant_key="t1",
        user_id="u1",
        roles=roles,
    )


def _result(**overrides) -> OrchidPipelineResult:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "run_id": "run-1",
        "pipeline": "demo",
        "status": "succeeded",
        "summary": "All good.",
        "output": {"ok": True},
        "graph": {"objects": [{"id": "o1"}]},
        "graph_stats": {"objects": 1},
        "jobs": {"load": {"status": "succeeded"}},
        "started_at": now,
        "finished_at": now,
        "metadata": {},
    }
    values.update(overrides)
    return OrchidPipelineResult(**values)


class _FakeOrchid:
    def __init__(
        self,
        registry: Any,
        *,
        result: Any = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.pipelines = registry
        self.config = types.SimpleNamespace(events=None)
        self.result = result or _result()
        self.error = error
        self.delay = delay
        self.run_calls: list[dict[str, Any]] = []
        self.chat_repo = AsyncMock()

    async def run_pipeline(self, name, input, **kwargs):
        self.run_calls.append({"name": name, "input": input, **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


class _FakeLauncher:
    def __init__(self, result: PipelineLaunchResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def launch(self, name, input, **kwargs):
        self.calls.append({"name": name, "input": input, **kwargs})
        return self.result


def _app(
    *,
    registry: Any | None = None,
    orchid: Any | None = None,
    auth: OrchidAuthContext | None = None,
    settings: Settings | None = None,
    override_auth: bool = True,
) -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(PipelineAPIError, pipeline_error_handler)
    app.include_router(pipelines_router.router)
    if override_auth:
        user = auth or _auth()
        app.dependency_overrides[pipelines_router.require_pipeline_auth] = lambda: user
    if registry is not None:
        app.dependency_overrides[pipelines_router.get_pipelines] = lambda: registry
    if orchid is not None:
        app.dependency_overrides[pipelines_router._get_orchid] = lambda: orchid
    if settings is not None:
        app.dependency_overrides[pipelines_router.get_settings] = lambda: settings
    return app


def _client(**kwargs) -> TestClient:
    return TestClient(_app(**kwargs))


def _error_code(response) -> str:
    return response.json()["error"]["code"]


# ── Listing and detail ─────────────────────────────────────


def test_list_pipelines_is_paginated_and_sanitized(p016_steps: str) -> None:
    registry = _registry()
    with _client(registry=registry) as client:
        response = client.get("/pipelines", params={"limit": 1, "offset": 0})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["name"] == "demo"
    assert item["input_schema"]["properties"]["account_id"]
    assert item["expose"]["as_tool"] is True
    assert item["expose"]["requires_approval"] is False
    assert item["expose"]["parallel_safe"] is False
    assert item["expose"]["require_role"] is None
    assert item["expose"]["max_tool_result_chars"] == 8000
    assert item["expose"]["interpretation"] is None
    assert "tool_name" not in response.text
    assert "prompt" not in response.text


def test_list_limit_is_capped(p016_steps: str) -> None:
    with _client(registry=_registry()) as client:
        response = client.get("/pipelines", params={"limit": 500})

    assert response.status_code == 200
    assert response.json()["limit"] == 200


def test_get_pipeline_is_sanitized(p016_steps: str) -> None:
    with _client(registry=_registry()) as client:
        response = client.get("/pipelines/demo")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "demo"
    assert body["description"] == "Demo pipeline"
    assert body["jobs"] == [{"id": "load", "kind": "deterministic", "depends_on": []}]
    assert "tool_name" not in response.text
    assert "p016_steps" not in response.text


def test_detail_displays_require_role(p016_steps: str) -> None:
    registry = _registry(expose_extra={"require_role": "admin"})
    with _client(registry=registry) as client:
        response = client.get("/pipelines/demo")

    assert response.status_code == 200
    assert response.json()["expose"]["require_role"] == "admin"


def test_unknown_pipeline_is_404(p016_steps: str) -> None:
    registry = _registry()
    with _client(registry=registry, orchid=_FakeOrchid(registry)) as client:
        detail = client.get("/pipelines/missing")
        run = client.post("/pipelines/missing/runs", json={"input": {"account_id": "a1"}})
        validate = client.post("/pipelines/missing/validate", json={"input": {}})

    for response in (detail, run, validate):
        assert response.status_code == 404
        assert _error_code(response) == "pipeline_not_found"


def test_list_requires_auth(p016_steps: str) -> None:
    registry = _registry()
    app = _app(registry=registry, orchid=_FakeOrchid(registry), override_auth=False)
    with TestClient(app) as client:
        response = client.get("/pipelines")

    assert response.status_code == 401
    assert _error_code(response) == "pipeline_unauthorized"


def test_uninitialised_registry_is_503() -> None:
    app = FastAPI()
    app.add_exception_handler(PipelineAPIError, pipeline_error_handler)
    app.include_router(pipelines_router.router)
    app.dependency_overrides[pipelines_router.require_pipeline_auth] = lambda: _auth()
    with TestClient(app) as client:
        response = client.get("/pipelines")

    assert response.status_code == 503


# ── Auth, role and rate limit ──────────────────────────────


def test_missing_bearer_is_401(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(registry)
    app = _app(registry=registry, orchid=orchid, override_auth=False)
    with TestClient(app) as client:
        response = client.post("/pipelines/demo/runs", json={"input": {"account_id": "a1"}})

    assert response.status_code == 401
    assert _error_code(response) == "pipeline_unauthorized"
    assert orchid.run_calls == []


def test_invalid_bearer_is_401(p016_steps: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _reject(**kwargs):
        raise HTTPException(status_code=401, detail="bad token")

    monkeypatch.setattr(pipelines_router, "get_auth_context", _reject)
    registry = _registry()
    app = _app(registry=registry, orchid=_FakeOrchid(registry), override_auth=False)
    with TestClient(app) as client:
        response = client.post(
            "/pipelines/demo/runs",
            json={"input": {"account_id": "a1"}},
            headers={"Authorization": "Bearer bad"},
        )

    assert response.status_code == 401
    assert _error_code(response) == "pipeline_unauthorized"


def test_role_restricted_pipeline_is_403(p016_steps: str) -> None:
    registry = _registry(expose_extra={"require_role": "admin"})
    orchid = _FakeOrchid(registry)
    with _client(registry=registry, orchid=orchid, auth=_auth()) as client:
        response = client.post("/pipelines/demo/runs", json={"input": {"account_id": "a1"}})

    assert response.status_code == 403
    assert _error_code(response) == "pipeline_forbidden"
    assert orchid.run_calls == []


def test_rate_limit_returns_429_with_retry_after(p016_steps: str) -> None:
    app_ctx.pipeline_limiter = None
    app_ctx.pipeline_limiter_limit = 0
    registry = _registry()
    orchid = _FakeOrchid(registry)
    settings = Settings(pipeline_rate_limit_per_minute=2)
    with _client(registry=registry, orchid=orchid, settings=settings) as client:
        first = client.post("/pipelines/demo/runs", json={"input": {"account_id": "a1"}})
        second = client.post("/pipelines/demo/runs", json={"input": {"account_id": "a1"}})
        third = client.post("/pipelines/demo/runs", json={"input": {"account_id": "a1"}})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert _error_code(third) == "pipeline_rate_limited"
    assert int(third.headers["Retry-After"]) >= 1


# ── Validation ─────────────────────────────────────────────


def test_validate_reports_field_errors_without_running(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(registry)
    with _client(registry=registry, orchid=orchid) as client:
        invalid = client.post("/pipelines/demo/validate", json={"input": {}})
        valid = client.post("/pipelines/demo/validate", json={"input": {"account_id": "a1"}})

    assert invalid.status_code == 200
    assert invalid.json()["valid"] is False
    assert invalid.json()["errors"]
    assert valid.json() == {"valid": True, "errors": []}
    assert orchid.run_calls == []


def test_malformed_json_is_400(p016_steps: str) -> None:
    registry = _registry()
    with _client(registry=registry, orchid=_FakeOrchid(registry)) as client:
        response = client.post(
            "/pipelines/demo/runs",
            content="{not json",
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert _error_code(response) == "pipeline_input_invalid"


def test_input_schema_violation_is_422(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(registry)
    with _client(registry=registry, orchid=orchid) as client:
        response = client.post("/pipelines/demo/runs", json={"input": {}})

    assert response.status_code == 422
    assert _error_code(response) == "pipeline_input_schema_invalid"
    assert orchid.run_calls == []


# ── Sync runs ──────────────────────────────────────────────


def test_sync_run_returns_result_without_a_graph_by_default(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(registry)
    with _client(registry=registry, orchid=orchid) as client:
        response = client.post(
            "/pipelines/demo/runs",
            json={"input": {"account_id": "a1"}, "include_graph": False},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["summary"] == "All good."
    assert body["output"] == {"ok": True}
    assert body["graph_stats"] == {"objects": 1}
    assert "graph" not in body
    [call] = orchid.run_calls
    assert call["mode"] == "direct"
    assert call["timeout"] == 300
    assert call["include_graph"] is False
    orchid.chat_repo.add_message.assert_not_called()
    orchid.chat_repo.create_chat.assert_not_called()


def test_sync_run_returns_the_graph_when_requested(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(registry)
    with _client(registry=registry, orchid=orchid) as client:
        response = client.post(
            "/pipelines/demo/runs",
            json={"input": {"account_id": "a1"}, "include_graph": True},
        )

    assert response.status_code == 200
    assert response.json()["graph"] == {"objects": [{"id": "o1"}]}


def test_sync_run_executed_failure_is_200(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(registry, result=_result(status="failed", summary="", error="job boom"))
    with _client(registry=registry, orchid=orchid) as client:
        response = client.post("/pipelines/demo/runs", json={"input": {"account_id": "a1"}})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == "job boom"


def test_sync_run_timeout_is_504(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(registry, error=OrchidPipelineTimeoutError("deadline"))
    with _client(registry=registry, orchid=orchid) as client:
        response = client.post("/pipelines/demo/runs", json={"input": {"account_id": "a1"}})

    assert response.status_code == 504
    assert _error_code(response) == "pipeline_timeout"


def test_sync_run_deadline_failure_is_504(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(
        registry,
        result=_result(status="failed", summary="", error="pipeline exceeded its deadline"),
        delay=0.12,
    )
    with _client(registry=registry, orchid=orchid) as client:
        response = client.post(
            "/pipelines/demo/runs",
            json={"input": {"account_id": "a1"}, "timeout_seconds": 0.1},
        )

    assert response.status_code == 504
    assert _error_code(response) == "pipeline_timeout"


def test_sync_request_deadline_failure_is_504(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(
        registry,
        result=_result(status="failed", summary="", error="pipeline run was cancelled", jobs={}),
        delay=0.12,
    )
    with _client(registry=registry, orchid=orchid) as client:
        response = client.post(
            "/pipelines/demo/runs",
            json={"input": {"account_id": "a1"}, "timeout_seconds": 0.1},
        )

    assert response.status_code == 504
    assert _error_code(response) == "pipeline_timeout"


def test_sync_run_graph_cap_is_413(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(registry, result=_result(metadata={"graph_omitted": "size"}))
    with _client(registry=registry, orchid=orchid) as client:
        response = client.post(
            "/pipelines/demo/runs",
            json={"input": {"account_id": "a1"}, "include_graph": True},
        )

    assert response.status_code == 413
    assert _error_code(response) == "pipeline_graph_too_large"


# ── Async runs ─────────────────────────────────────────────


def test_async_run_is_202_and_propagates_idempotency(p016_steps: str, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry()
    launcher = _FakeLauncher(
        PipelineLaunchResult(
            status="accepted",
            pipeline="demo",
            signal_id="sig-1",
            message="Started",
        )
    )
    monkeypatch.setattr(pipelines_router, "_build_launcher", lambda registry, orchid: launcher)
    with _client(registry=registry, orchid=_FakeOrchid(registry)) as client:
        response = client.post(
            "/pipelines/demo/runs",
            json={"input": {"account_id": "a1"}, "mode": "async"},
            headers={"Idempotency-Key": "idem-1"},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["signal_id"] == "sig-1"
    assert body["run_id"] is None
    assert body["status_url"] == "/jobs/demo-trigger/runs"
    [call] = launcher.calls
    assert call["dedupe_key"] == "t1:u1:idem-1"


def test_async_unavailable_is_409(p016_steps: str, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry()
    launcher = _FakeLauncher(
        PipelineLaunchResult(
            status="failed",
            pipeline="demo",
            error={"code": "pipeline_async_unavailable", "message": "events disabled"},
        )
    )
    monkeypatch.setattr(pipelines_router, "_build_launcher", lambda registry, orchid: launcher)
    with _client(registry=registry, orchid=_FakeOrchid(registry)) as client:
        response = client.post(
            "/pipelines/demo/runs",
            json={"input": {"account_id": "a1"}, "mode": "async"},
        )

    assert response.status_code == 409
    assert _error_code(response) == "pipeline_async_unavailable"


def test_async_body_idempotency_key_is_used_when_header_is_absent(
    p016_steps: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry()
    launcher = _FakeLauncher(PipelineLaunchResult(status="accepted", pipeline="demo", signal_id="sig-2"))
    monkeypatch.setattr(pipelines_router, "_build_launcher", lambda registry, orchid: launcher)
    with _client(registry=registry, orchid=_FakeOrchid(registry)) as client:
        response = client.post(
            "/pipelines/demo/runs",
            json={"input": {"account_id": "a1"}, "mode": "async", "idempotency_key": "body-idem"},
        )

    assert response.status_code == 202
    assert launcher.calls[0]["dedupe_key"] == "t1:u1:body-idem"


async def test_api_pipeline_runner_maps_bloom_to_chat(p016_steps: str, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry()
    fake = _FakeOrchid(registry)
    monkeypatch.setattr(api_lifecycle.app_ctx, "orchid", fake)

    result = await api_lifecycle._APIPipelineRunner().run(
        "demo",
        {"account_id": "a1"},
        auth=_auth(),
        mode="bloom",
        chat_id="c-1",
        correlation_id="corr-1",
        include_graph=True,
    )

    assert result is fake.result
    [call] = fake.run_calls
    assert call["name"] == "demo"
    assert call["input"] == {"account_id": "a1"}
    assert call["mode"] == "chat"
    assert call["chat_id"] == "c-1"
    assert call["correlation_id"] == "corr-1"
    assert call["include_graph"] is True


def test_run_requires_an_input_object(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(registry)
    with _client(registry=registry, orchid=orchid) as client:
        missing = client.post("/pipelines/demo/runs", json={})
        null = client.post("/pipelines/demo/runs", json={"input": None})
        seed_only = client.post("/pipelines/demo/runs", json={"seed": []})

    for response in (missing, null, seed_only):
        assert response.status_code == 400
        assert _error_code(response) == "pipeline_input_invalid"
    assert orchid.run_calls == []


def test_validate_returns_valid_false_for_a_non_object_input(p016_steps: str) -> None:
    registry = _registry()
    with _client(registry=registry, orchid=_FakeOrchid(registry)) as client:
        response = client.post("/pipelines/demo/validate", json={"input": []})

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["errors"]


def test_async_rejects_sync_only_fields(p016_steps: str) -> None:
    registry = _registry()
    with _client(registry=registry, orchid=_FakeOrchid(registry)) as client:
        response = client.post(
            "/pipelines/demo/runs",
            json={"input": {"account_id": "a1"}, "mode": "async", "include_graph": True},
        )

    assert response.status_code == 400
    assert _error_code(response) == "pipeline_input_invalid"


def test_sync_job_timeout_is_an_executed_failure(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(
        registry,
        result=_result(
            status="failed",
            summary="",
            error="run failed",
            jobs={"load": {"status": "failed", "error": "load timed out after 5s"}},
        ),
    )
    with _client(registry=registry, orchid=orchid) as client:
        response = client.post("/pipelines/demo/runs", json={"input": {"account_id": "a1"}})

    assert response.status_code == 200
    assert response.json()["status"] == "failed"


def test_unexpected_run_error_is_stable_500(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(registry, error=RuntimeError("boom"))
    with _client(registry=registry, orchid=orchid) as client:
        response = client.post("/pipelines/demo/runs", json={"input": {"account_id": "a1"}})

    assert response.status_code == 500
    assert _error_code(response) == "pipeline_internal_error"
    assert "boom" not in response.text


async def test_async_dedupe_key_is_scoped_to_tenant_and_user(p016_steps: str) -> None:
    registry = _registry()
    definition = registry.get("demo")
    launcher = _FakeLauncher(PipelineLaunchResult(status="accepted", pipeline="demo", signal_id="sig"))
    await pipelines_router._run_async(
        "demo",
        definition,
        {},
        auth=_auth(),
        launcher=launcher,
        idempotency_key="same",
        timeout_seconds=None,
    )
    other = OrchidAuthContext(access_token="t", tenant_key="t2", user_id="u2")
    await pipelines_router._run_async(
        "demo",
        definition,
        {},
        auth=other,
        launcher=launcher,
        idempotency_key="same",
        timeout_seconds=None,
    )

    assert launcher.calls[0]["dedupe_key"] == "t1:u1:same"
    assert launcher.calls[1]["dedupe_key"] == "t2:u2:same"


def test_unbounded_timeout_is_rejected(p016_steps: str) -> None:
    registry = _registry()
    with _client(registry=registry, orchid=_FakeOrchid(registry)) as client:
        response = client.post(
            "/pipelines/demo/runs",
            content='{"input": {"account_id": "a1"}, "timeout_seconds": 1e309}',
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 422
    assert _error_code(response) == "pipeline_input_schema_invalid"


@pytest.mark.parametrize(
    ("code", "status_code"),
    [
        ("pipeline_input_error", 400),
        ("pipeline_auth_error", 403),
        ("pipeline_async_emit_failed", 503),
        ("pipeline_async_unavailable", 409),
    ],
)
def test_async_launcher_errors_map_to_stable_statuses(
    p016_steps: str,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    status_code: int,
) -> None:
    registry = _registry()
    launcher = _FakeLauncher(
        PipelineLaunchResult(
            status="failed",
            pipeline="demo",
            error={"code": code, "message": "launch failed"},
        )
    )
    monkeypatch.setattr(pipelines_router, "_build_launcher", lambda registry, orchid: launcher)
    with _client(registry=registry, orchid=_FakeOrchid(registry)) as client:
        response = client.post(
            "/pipelines/demo/runs",
            json={"input": {"account_id": "a1"}, "mode": "async"},
        )

    assert response.status_code == status_code
    assert _error_code(response) == code


def test_stream_missing_input_is_400(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(registry)
    with _client(registry=registry, orchid=orchid) as client:
        response = client.post("/pipelines/demo/runs/stream", json={})

    assert response.status_code == 400
    assert _error_code(response) == "pipeline_input_invalid"


def test_sync_idempotency_key_is_rejected(p016_steps: str) -> None:
    registry = _registry()
    orchid = _FakeOrchid(registry)
    with _client(registry=registry, orchid=orchid) as client:
        response = client.post(
            "/pipelines/demo/runs",
            json={"input": {"account_id": "a1"}, "idempotency_key": "sync-key"},
        )

    assert response.status_code == 400
    assert _error_code(response) == "pipeline_input_invalid"
    assert orchid.run_calls == []


def test_validate_unknown_pipeline_wins_over_a_bad_input(p016_steps: str) -> None:
    registry = _registry()
    with _client(registry=registry, orchid=_FakeOrchid(registry)) as client:
        response = client.post("/pipelines/missing/validate", json={"input": []})

    assert response.status_code == 404
    assert _error_code(response) == "pipeline_not_found"
