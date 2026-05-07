"""Tests for ``GET /chats/{chat_id}/events/stream`` (Phase 4.5 §LS6/LS7).

Coverage:

- 200 + SSE headers for the chat owner.
- 404 (never 403) for non-owners and cross-tenant callers.
- 404 for non-existent chats.
- Discovery: in-flight chat-bound runs surface as
  ``chat.bloom.attached`` events on connect.
- Live forwarding: events published on the in-process
  ``chat:{chat_id}`` channel reach the SSE consumer.
- ``_format_chat_sse`` wire format.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchid_ai.core.events.dispatcher import OrchidSignalDispatcher
from orchid_ai.core.events.job import JobRun, JobSpec, JobStatus
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.events.queues.inmemory import (
    InMemoryJobStore,
    InMemoryScheduleStore,
    InMemorySignalQueue,
    InMemorySignalStore,
    InMemoryTriggerStore,
)
from orchid_ai.events.registry import InMemoryTriggerRegistry
from orchid_ai.events.streaming import BloomEventStream, ChatBloomEvent

from orchid_api.context import app_ctx
from orchid_api.events_bootstrap import EventsRuntime
from orchid_api.routers import chat_events


# ── Fixtures ────────────────────────────────────────────────


def _override_auth(*, user_id: str = "u-7", tenant: str = "t-1", roles: set[str] | None = None):
    def _dep():
        return OrchidAuthContext(
            access_token="t",
            tenant_key=tenant,
            user_id=user_id,
            roles=roles or set(),
        )

    return _dep


@pytest.fixture
def chat_storage():
    """Tiny in-memory chat-storage stub for the ``require_chat_owner_or_admin`` dep.

    Mirrors only the surface the dep touches (``get_chat(chat_id)``)
    plus a helper to seed rows.  Avoids spinning up a real
    SQLite/Postgres chat storage just to check the 404 contract.
    """

    class _Stub:
        def __init__(self):
            self._chats: dict[str, object] = {}

        async def create_chat(self, *, tenant_id: str, user_id: str, title: str):
            chat_id = f"C-{len(self._chats) + 1}"

            class _Chat:
                pass

            chat = _Chat()
            chat.id = chat_id
            chat.tenant_id = tenant_id
            chat.user_id = user_id
            chat.title = title
            self._chats[chat_id] = chat
            return chat

        async def get_chat(self, chat_id: str):
            return self._chats.get(chat_id)

    return _Stub()


@pytest.fixture
def test_runtime():
    """Wire an in-memory ``EventsRuntime`` onto ``app_ctx``."""
    queue = InMemorySignalQueue()
    signal_store = InMemorySignalStore()
    job_store = InMemoryJobStore()
    schedule_store = InMemoryScheduleStore()
    trigger_store = InMemoryTriggerStore()
    dispatcher = OrchidSignalDispatcher(store=signal_store, queue=queue)
    registry = InMemoryTriggerRegistry()
    event_stream = BloomEventStream(idle_timeout_seconds=0.5)

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


def _make_app(
    *,
    chat_storage,
    user_id: str = "u-7",
    tenant: str = "t-1",
    roles: set[str] | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(chat_events.router)

    from orchid_api.auth import get_auth_context
    from orchid_api.context import get_chat_repo

    app.dependency_overrides[get_auth_context] = _override_auth(user_id=user_id, tenant=tenant, roles=roles)
    app.dependency_overrides[get_chat_repo] = lambda: chat_storage
    return app


def _make_run(
    *,
    chat_id: str,
    status: JobStatus = JobStatus.RUNNING,
    user_id: str = "u-7",
    source_message_id: str | None = "m-99",
) -> JobRun:
    spec = JobSpec(
        trigger_id="deep-research",
        signal_id=_uuid.uuid4(),
        agent_name="reviews",
        prompt="p",
        identity_claim={"mode": "act_as_user", "user_id": user_id},
        correlation_id="corr",
        parallelism_key=f"user:t-1:{user_id}",
        visibility="actor",
        visibility_user_id=user_id,
        chat_binding={
            "chat_id": chat_id,
            "mode": "append_final_message",
            "on_failure": "post_error",
            "source_message_id": source_message_id,
        },
    )
    return JobRun(
        run_id=_uuid.uuid4(),
        spec=spec,
        attempt_number=1,
        status=status,
        queued_at=_dt.datetime.now(tz=_dt.UTC),
        started_at=_dt.datetime.now(tz=_dt.UTC),
    )


# ── Authorization (404-never-403) ───────────────────────────


async def test_owner_gets_200_with_sse_headers(test_runtime, chat_storage) -> None:
    chat = await chat_storage.create_chat(tenant_id="t-1", user_id="u-7", title="alice")
    app = _make_app(chat_storage=chat_storage)
    with TestClient(app) as client:
        with client.stream("GET", f"/chats/{chat.id}/events/stream") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            assert response.headers["cache-control"] == "no-cache, no-transform"
            assert response.headers["x-accel-buffering"] == "no"


async def test_non_owner_gets_404_not_403(test_runtime, chat_storage) -> None:
    """A different user in the same tenant gets 404 (never 403)."""
    chat = await chat_storage.create_chat(tenant_id="t-1", user_id="u-OWNER", title="owned")
    app = _make_app(chat_storage=chat_storage, user_id="u-INTRUDER")
    with TestClient(app) as client:
        response = client.get(f"/chats/{chat.id}/events/stream")
        assert response.status_code == 404


async def test_cross_tenant_gets_404_even_for_admin(test_runtime, chat_storage) -> None:
    """Cross-tenant access is always 404, even for admins."""
    chat = await chat_storage.create_chat(tenant_id="t-1", user_id="u-7", title="t1-chat")
    app = _make_app(
        chat_storage=chat_storage,
        tenant="t-OTHER",
        roles={"admin"},
        user_id="u-admin",
    )
    with TestClient(app) as client:
        response = client.get(f"/chats/{chat.id}/events/stream")
        assert response.status_code == 404


async def test_admin_in_same_tenant_passes_owner_check(test_runtime, chat_storage) -> None:
    """An admin in the same tenant can subscribe to another user's chat."""
    chat = await chat_storage.create_chat(tenant_id="t-1", user_id="u-7", title="user-chat")
    app = _make_app(
        chat_storage=chat_storage,
        user_id="u-admin",
        roles={"admin"},
    )
    with TestClient(app) as client:
        with client.stream("GET", f"/chats/{chat.id}/events/stream") as response:
            assert response.status_code == 200


def test_nonexistent_chat_returns_404(test_runtime, chat_storage) -> None:
    """No chat row → 404 (same shape as not-an-owner)."""
    app = _make_app(chat_storage=chat_storage)
    with TestClient(app) as client:
        response = client.get("/chats/does-not-exist/events/stream")
        assert response.status_code == 404


# ── Discovery + live forwarding ─────────────────────────────


async def test_discovery_emits_attached_for_in_flight_bound_runs(test_runtime, chat_storage) -> None:
    """On connect, in-flight chat-bound runs surface as ``chat.bloom.attached``."""
    chat = await chat_storage.create_chat(tenant_id="t-1", user_id="u-7", title="alice")
    in_flight = _make_run(chat_id=chat.id, status=JobStatus.RUNNING)
    finished = _make_run(chat_id=chat.id, status=JobStatus.SUCCEEDED)
    other_chat_run = _make_run(chat_id="C-OTHER", status=JobStatus.RUNNING)
    await test_runtime.job_store.insert(in_flight)
    await test_runtime.job_store.insert(finished)
    await test_runtime.job_store.insert(other_chat_run)

    app = _make_app(chat_storage=chat_storage)
    with TestClient(app) as client:
        with client.stream("GET", f"/chats/{chat.id}/events/stream") as response:
            assert response.status_code == 200
            # Read the stream body until the idle timeout closes it.
            body = b""
            for chunk in response.iter_bytes():
                body += chunk

    text = body.decode("utf-8")
    # Exactly one ``chat.bloom.attached`` event for the in-flight run.
    assert text.count("event: chat.bloom.attached") == 1
    # The finished run is NOT in discovery.
    # The other-chat run is NOT in discovery (filtered by chat_binding_chat_id).
    assert str(in_flight.run_id) in text
    assert str(finished.run_id) not in text
    assert str(other_chat_run.run_id) not in text
    # Wire-format sanity.
    assert "data: {" in text


def test_format_chat_sse_wire_shape() -> None:
    """``_format_chat_sse`` produces ``event: <type>\\ndata: {...}\\n\\n``."""
    occurred = _dt.datetime(2026, 5, 7, 12, 0, 0, tzinfo=_dt.UTC)
    event = ChatBloomEvent(
        type="chat.bloom.attached",
        chat_id="C-1",
        run_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
        occurred_at=occurred,
        payload={"trigger_id": "t", "source_message_id": "m-1"},
    )
    rendered = chat_events._format_chat_sse(event)
    assert rendered.startswith("event: chat.bloom.attached\n")
    assert "data: {" in rendered
    assert rendered.endswith("\n\n")
    # Parse the data line.
    import json

    data_line = [ln for ln in rendered.splitlines() if ln.startswith("data: ")][0]
    parsed = json.loads(data_line[len("data: ") :])
    assert parsed["type"] == "chat.bloom.attached"
    assert parsed["chat_id"] == "C-1"
    assert parsed["run_id"] == "00000000-0000-0000-0000-000000000001"
    assert parsed["occurred_at"] == occurred.isoformat()
    assert parsed["payload"]["trigger_id"] == "t"


def test_synthetic_attached_for_carries_binding_metadata() -> None:
    """``_synthetic_attached_for`` reads from the run.spec.chat_binding."""
    run = _make_run(chat_id="C-7", source_message_id="m-99")
    event = chat_events._synthetic_attached_for(run)
    assert event.type == "chat.bloom.attached"
    assert event.chat_id == "C-7"
    assert event.run_id == run.run_id
    assert event.payload["source_message_id"] == "m-99"
    assert event.payload["identity_mode"] == "act_as_user"
    assert event.payload["trigger_id"] == "deep-research"


def test_synthetic_attached_for_handles_missing_source_message_id() -> None:
    """A bound run without ``source_message_id`` still produces a valid event."""
    run = _make_run(chat_id="C-7", source_message_id=None)
    event = chat_events._synthetic_attached_for(run)
    assert event.payload["source_message_id"] is None
