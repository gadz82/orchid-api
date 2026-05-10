"""End-to-end tests for the four Phase-4 event routers.

Each test boots a tiny FastAPI app that mounts the routers + a
hand-rolled :class:`EventsRuntime` backed by in-memory stores, then
hits the endpoints with a TestClient.  Visibility (§26) is exercised
across the (caller role, run visibility) matrix per §26.10.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchid_ai.core.events.dispatcher import OrchidSignalDispatcher
from orchid_ai.core.events.job import JobRun, JobSpec, JobStatus
from orchid_ai.core.events.signal import Signal
from orchid_ai.core.events.store import OrchidScheduleRecord
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.events.queues.inmemory import (
    InMemoryJobStore,
    InMemoryScheduleStore,
    InMemorySignalQueue,
    InMemorySignalStore,
    InMemoryTriggerStore,
)
from orchid_ai.events.registry import InMemoryTriggerRegistry
from orchid_ai.events.streaming import (
    BloomEventStream,
    finished_event,
)

from orchid_api.context import app_ctx
from orchid_api.events_bootstrap import EventsRuntime
from orchid_api.routers import jobs, runs, schedules, signals


# ── Test app + fixtures ────────────────────────────────────


def _override_auth(roles: set[str] | None = None, user_id: str = "u-7"):
    def _dep():
        return OrchidAuthContext(
            access_token="t",
            tenant_key="t-1",
            user_id=user_id,
            roles=roles or set(),
        )

    return _dep


@pytest.fixture
def test_runtime():
    """Build a fully wired in-memory ``EventsRuntime`` and stash it
    on ``app_ctx`` for the duration of the test."""
    queue = InMemorySignalQueue()
    signal_store = InMemorySignalStore()
    job_store = InMemoryJobStore()
    schedule_store = InMemoryScheduleStore()
    trigger_store = InMemoryTriggerStore()
    dispatcher = OrchidSignalDispatcher(store=signal_store, queue=queue)
    registry = InMemoryTriggerRegistry()
    event_stream = BloomEventStream()

    runtime = EventsRuntime(
        enabled=True,
        dispatcher=dispatcher,
        signal_store=signal_store,
        signal_queue=queue,
        job_store=job_store,
        schedule_store=schedule_store,
        trigger_store=trigger_store,
        trigger_registry=registry,
        event_stream=event_stream,
    )

    previous = app_ctx.events
    app_ctx.events = runtime
    yield runtime
    app_ctx.events = previous


def _make_app(*, auth_roles: set[str] | None = None, user_id: str = "u-7") -> FastAPI:
    app = FastAPI()
    app.include_router(signals.router)
    app.include_router(jobs.router)
    app.include_router(runs.router)
    app.include_router(schedules.router)

    from orchid_api.auth import get_auth_context

    app.dependency_overrides[get_auth_context] = _override_auth(roles=auth_roles, user_id=user_id)
    return app


def _seed_signal(store: InMemorySignalStore, *, tenant: str = "t-1") -> Signal:
    sig = Signal(
        type="x",
        payload={"k": "v"},
        source="src",
        occurred_at=_dt.datetime.now(tz=_dt.UTC),
        tenant_key=tenant,
        signal_id=_uuid.uuid4(),
        persisted_at=_dt.datetime.now(tz=_dt.UTC),
        user_id="u-7",
    )

    import asyncio

    asyncio.get_event_loop().run_until_complete(store.insert(sig))
    return sig


def _seed_run(
    job_store: InMemoryJobStore,
    *,
    visibility: str = "actor",
    visibility_user_id: str | None = "u-7",
    signal_id: _uuid.UUID | None = None,
    trigger_id: str = "t1",
    tenant: str = "t-1",
) -> JobRun:
    spec = JobSpec(
        trigger_id=trigger_id,
        signal_id=signal_id or _uuid.uuid4(),
        agent_name="agent",
        prompt="x",
        identity_claim={"mode": "act_as_user", "user_id": "u-7"},
        correlation_id=None,
        parallelism_key=f"sa:{tenant}:bot",
        visibility=visibility,
        visibility_user_id=visibility_user_id,
    )
    run = JobRun(
        run_id=_uuid.uuid4(),
        spec=spec,
        attempt_number=1,
        status=JobStatus.SUCCEEDED,
        queued_at=_dt.datetime.now(tz=_dt.UTC),
        started_at=_dt.datetime.now(tz=_dt.UTC),
        finished_at=_dt.datetime.now(tz=_dt.UTC),
        result={"final_response": "done"},
    )

    import asyncio

    asyncio.get_event_loop().run_until_complete(job_store.insert(run))
    return run


# ── /signals ────────────────────────────────────────────────


def test_get_signal_admin_can_see_any(test_runtime) -> None:
    sig = _seed_signal(test_runtime.signal_store)
    app = _make_app(auth_roles={"admin"})
    with TestClient(app) as client:
        resp = client.get(f"/signals/{sig.signal_id}")
    assert resp.status_code == 200
    assert resp.json()["signal_id"] == str(sig.signal_id)


def test_get_signal_returns_404_for_nonexistent(test_runtime) -> None:
    app = _make_app(auth_roles={"admin"})
    with TestClient(app) as client:
        resp = client.get(f"/signals/{_uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_signal_returns_404_cross_tenant_even_for_admin(test_runtime) -> None:
    sig = _seed_signal(test_runtime.signal_store, tenant="t-OTHER")
    app = _make_app(auth_roles={"admin"})
    with TestClient(app) as client:
        resp = client.get(f"/signals/{sig.signal_id}")
    assert resp.status_code == 404


def test_get_signal_user_can_see_own_signal_via_runs(test_runtime) -> None:
    sig = _seed_signal(test_runtime.signal_store)
    _seed_run(
        test_runtime.job_store,
        visibility="actor",
        visibility_user_id="u-7",
        signal_id=sig.signal_id,
    )
    app = _make_app()
    with TestClient(app) as client:
        resp = client.get(f"/signals/{sig.signal_id}")
    assert resp.status_code == 200


def test_get_signal_other_user_cannot_see(test_runtime) -> None:
    sig = _seed_signal(test_runtime.signal_store)
    _seed_run(
        test_runtime.job_store,
        visibility="actor",
        visibility_user_id="u-OTHER",
        signal_id=sig.signal_id,
    )
    app = _make_app(user_id="u-7")
    with TestClient(app) as client:
        resp = client.get(f"/signals/{sig.signal_id}")
    assert resp.status_code == 404


def test_list_signals_admin_only(test_runtime) -> None:
    _seed_signal(test_runtime.signal_store)
    # Non-admin → 404 (not 403 per §26.6).
    app = _make_app()
    with TestClient(app) as client:
        resp = client.get("/signals")
    assert resp.status_code == 404


def test_list_signals_admin_path(test_runtime) -> None:
    _seed_signal(test_runtime.signal_store)
    app = _make_app(auth_roles={"admin"})
    with TestClient(app) as client:
        resp = client.get("/signals")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


def test_replay_signal_requires_admin(test_runtime) -> None:
    sig = _seed_signal(test_runtime.signal_store)
    app = _make_app()  # no admin role
    with TestClient(app) as client:
        resp = client.post(f"/signals/{sig.signal_id}/replay")
    assert resp.status_code == 404


def test_replay_signal_admin_path_requeues(test_runtime) -> None:
    sig = _seed_signal(test_runtime.signal_store)
    app = _make_app(auth_roles={"admin"})
    with TestClient(app) as client:
        resp = client.post(f"/signals/{sig.signal_id}/replay")
    assert resp.status_code == 200
    body = resp.json()
    assert body["signal_id"] == str(sig.signal_id)
    assert body["queue_msg_id"]


# ── /runs ───────────────────────────────────────────────────


def test_get_run_404_for_unknown_uuid(test_runtime) -> None:
    app = _make_app(auth_roles={"admin"})
    with TestClient(app) as client:
        resp = client.get(f"/runs/{_uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_run_404_for_malformed_uuid(test_runtime) -> None:
    app = _make_app(auth_roles={"admin"})
    with TestClient(app) as client:
        resp = client.get("/runs/not-a-uuid")
    assert resp.status_code == 404


def test_get_run_admin_sees_all(test_runtime) -> None:
    run = _seed_run(test_runtime.job_store, visibility="admin", visibility_user_id=None)
    app = _make_app(auth_roles={"admin"})
    with TestClient(app) as client:
        resp = client.get(f"/runs/{run.run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == str(run.run_id)
    assert body["visibility"] == "admin"
    assert body["result"] == {"final_response": "done"}


def test_get_run_actor_visibility_matches_owner(test_runtime) -> None:
    run = _seed_run(test_runtime.job_store, visibility="actor", visibility_user_id="u-7")
    app = _make_app(user_id="u-7")
    with TestClient(app) as client:
        resp = client.get(f"/runs/{run.run_id}")
    assert resp.status_code == 200


def test_get_run_actor_visibility_404_for_other_user(test_runtime) -> None:
    run = _seed_run(test_runtime.job_store, visibility="actor", visibility_user_id="u-7")
    # Caller is u-OTHER — must NEVER see u-7's run, even though same tenant.
    app = _make_app(user_id="u-OTHER")
    with TestClient(app) as client:
        resp = client.get(f"/runs/{run.run_id}")
    assert resp.status_code == 404


def test_get_run_admin_visibility_404_for_non_admin(test_runtime) -> None:
    run = _seed_run(test_runtime.job_store, visibility="admin", visibility_user_id=None)
    app = _make_app(user_id="u-7")  # no admin role
    with TestClient(app) as client:
        resp = client.get(f"/runs/{run.run_id}")
    assert resp.status_code == 404


def test_get_run_tenant_visibility_visible_to_any_authenticated(test_runtime) -> None:
    run = _seed_run(test_runtime.job_store, visibility="tenant", visibility_user_id=None)
    app = _make_app(user_id="u-OTHER")  # different user, same tenant
    with TestClient(app) as client:
        resp = client.get(f"/runs/{run.run_id}")
    assert resp.status_code == 200


def test_list_runs_filters_to_visible(test_runtime) -> None:
    own = _seed_run(test_runtime.job_store, visibility="actor", visibility_user_id="u-7")
    _seed_run(test_runtime.job_store, visibility="actor", visibility_user_id="u-OTHER")
    _seed_run(test_runtime.job_store, visibility="admin", visibility_user_id=None)

    app = _make_app(user_id="u-7")
    with TestClient(app) as client:
        resp = client.get("/runs")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["run_id"] == str(own.run_id)


def test_retry_creates_queue_msg(test_runtime) -> None:
    run = _seed_run(test_runtime.job_store, visibility="actor", visibility_user_id="u-7")
    # Insert the originating signal so the queue can re-enqueue it.
    sig = Signal(
        type="x",
        payload={},
        source="src",
        occurred_at=_dt.datetime.now(tz=_dt.UTC),
        tenant_key="t-1",
        signal_id=run.spec.signal_id,
        persisted_at=_dt.datetime.now(tz=_dt.UTC),
        user_id="u-7",
    )
    import asyncio

    asyncio.get_event_loop().run_until_complete(test_runtime.signal_store.insert(sig))

    app = _make_app(user_id="u-7")
    with TestClient(app) as client:
        resp = client.post(f"/runs/{run.run_id}/retry")
    assert resp.status_code == 200
    assert resp.json()["previous_run_id"] == str(run.run_id)
    assert resp.json()["queue_msg_id"]


def test_cancel_flips_status_to_cancelled(test_runtime) -> None:
    run = _seed_run(test_runtime.job_store, visibility="actor", visibility_user_id="u-7")
    # Override to a non-terminal status first
    run.status = JobStatus.RUNNING
    import asyncio

    asyncio.get_event_loop().run_until_complete(test_runtime.job_store.update(run))

    app = _make_app(user_id="u-7")
    with TestClient(app) as client:
        resp = client.post(f"/runs/{run.run_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


# ── /jobs ───────────────────────────────────────────────────


def test_list_jobs_returns_registered_triggers(test_runtime) -> None:
    """The trigger registry is empty in the fixture; we register one
    by hand to verify the surface."""

    class _FakeTrigger:
        @property
        def trigger_id(self) -> str:
            return "demo"

        @property
        def parallelism(self) -> str:
            return "per_user"

        @property
        def visibility(self) -> str:
            return "actor"

        @property
        def respect_chat_binding(self) -> bool:
            return False

        def matches(self, signal):  # pragma: no cover
            return False

        def build_job_spec(self, signal):  # pragma: no cover
            raise NotImplementedError

    test_runtime.trigger_registry.register(_FakeTrigger())

    app = _make_app(auth_roles={"admin"})
    with TestClient(app) as client:
        resp = client.get("/jobs")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["trigger_id"] == "demo"


def test_list_runs_for_unknown_trigger_returns_404(test_runtime) -> None:
    app = _make_app(auth_roles={"admin"})
    with TestClient(app) as client:
        resp = client.get("/jobs/unknown/runs")
    assert resp.status_code == 404


# ── /schedules ──────────────────────────────────────────────


def test_schedules_admin_only(test_runtime) -> None:
    app = _make_app()  # no admin
    with TestClient(app) as client:
        resp = client.get("/schedules")
    assert resp.status_code == 404


def test_schedules_list_admin(test_runtime) -> None:
    import asyncio

    asyncio.get_event_loop().run_until_complete(
        test_runtime.schedule_store.upsert(
            OrchidScheduleRecord(
                schedule_id="s1",
                trigger_id="t1",
                cron="0 * * * *",
                interval_seconds=None,
                identity_claim={"mode": "service_account", "name": "bot"},
                last_fire_at=None,
                next_fire_at=None,
                enabled=True,
            )
        )
    )
    app = _make_app(auth_roles={"admin"})
    with TestClient(app) as client:
        resp = client.get("/schedules")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


def test_schedules_patch_toggles_enabled(test_runtime) -> None:
    import asyncio

    asyncio.get_event_loop().run_until_complete(
        test_runtime.schedule_store.upsert(
            OrchidScheduleRecord(
                schedule_id="s1",
                trigger_id="t1",
                cron="0 * * * *",
                interval_seconds=None,
                identity_claim={"mode": "service_account", "name": "bot"},
                last_fire_at=None,
                next_fire_at=None,
                enabled=True,
            )
        )
    )
    app = _make_app(auth_roles={"admin"})
    with TestClient(app) as client:
        resp = client.patch("/schedules/s1", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_schedules_patch_unknown_returns_404(test_runtime) -> None:
    app = _make_app(auth_roles={"admin"})
    with TestClient(app) as client:
        resp = client.patch("/schedules/no-such", json={"enabled": False})
    assert resp.status_code == 404


# ── 503 when events disabled ────────────────────────────────


def test_event_endpoints_503_when_events_disabled() -> None:
    """When ``app_ctx.events`` is None / disabled, every event-driven
    endpoint must surface 503 — not crash with an AttributeError."""
    previous = app_ctx.events
    app_ctx.events = EventsRuntime(enabled=False)
    try:
        app = _make_app(auth_roles={"admin"})
        with TestClient(app) as client:
            assert client.get("/runs").status_code == 503
            assert client.get("/signals").status_code == 503
            assert client.get("/jobs").status_code == 503
            assert client.get("/schedules").status_code == 503
    finally:
        app_ctx.events = previous


# ── /runs/{id}/stream — visibility gate + format ────────────


def test_run_stream_authorization_blocks_unviewable_runs(test_runtime) -> None:
    """The SSE endpoint MUST run :func:`require_visible_run` on
    connect (per §26.7), so a non-admin caller asking to stream a
    different user's run gets a clean 404 — same shape as the JSON
    GET endpoint.  This is the security-critical part of the SSE
    contract; the wire-level happy path is covered by the
    ``BloomEventStream`` unit tests in ``orchid/tests/events``."""
    run = _seed_run(test_runtime.job_store, visibility="actor", visibility_user_id="u-7")
    app = _make_app(user_id="u-OTHER")
    with TestClient(app) as client:
        resp = client.get(f"/runs/{run.run_id}/stream")
    assert resp.status_code == 404


def test_run_stream_format_function_emits_sse_envelope() -> None:
    """The router formats events as ``event:`` + ``data:`` SSE
    frames.  Unit-test the helper directly to avoid the TestClient-
    vs-streaming-loop impedance mismatch (TestClient buffers SSE
    bodies until the connection closes; the helper itself is what
    we actually care about)."""
    from orchid_api.routers.runs import _format_sse

    event = finished_event(
        run_id=_uuid.uuid4(),
        status="succeeded",
        finished_at=_dt.datetime.now(tz=_dt.UTC),
        result={"ok": True},
    )
    formatted = _format_sse(event)
    assert formatted.startswith("event: bloom.run.finished\n")
    assert "data: " in formatted
    assert formatted.endswith("\n\n")
    import json as _json

    body = _json.loads(formatted.split("data: ", 1)[1].split("\n", 1)[0])
    assert body["type"] == "bloom.run.finished"
    assert body["payload"]["status"] == "succeeded"
