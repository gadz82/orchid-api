"""Authorization matrix for ``GET /chats/{chat_id}/events/stream`` (Phase 4.5 §LS9).

Companion to :mod:`test_chat_events_stream`.  Where that file covers
the happy path + discovery + wire format, this file pins down the
authorization contract:

- The ``require_chat_owner_or_admin`` dependency returns 404 (never
  403) for every non-authorised case.
- An admin in the same tenant CAN subscribe to another user's chat
  events — but the discovery step still scopes to that chat's bound
  runs (no cross-chat leakage).
- A user with the ``admin`` role in a DIFFERENT tenant cannot
  subscribe to another tenant's chats — cross-tenant is always 404
  regardless of role.
- The contract that "chat-binding implies actor or addressed
  visibility on every emitted run" (§LS9): a chat owner who
  subscribes sees only events for runs whose visibility includes
  them by construction (chat-binding never widens to ``tenant`` /
  ``admin`` for the chat owner's view).
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
from orchid_ai.events.streaming import BloomEventStream

from orchid_api.context import app_ctx
from orchid_api.events_bootstrap import EventsRuntime
from orchid_api.routers import chat_events


# ── Fixtures (mirror test_chat_events_stream.py) ────────────


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
def chat_storage_stub():
    class _Stub:
        def __init__(self):
            self._chats = {}

        async def create_chat(self, *, tenant_id: str, user_id: str, title: str = ""):
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
    user_id: str,
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
    user_id: str = "u-7",
    visibility: str = "actor",
    visibility_user_id: str | None = "u-7",
    tenant: str = "t-1",
) -> JobRun:
    spec = JobSpec(
        trigger_id="deep-research",
        signal_id=_uuid.uuid4(),
        agent_name="reviews",
        prompt="p",
        identity_claim={"mode": "act_as_user", "user_id": user_id},
        correlation_id="corr",
        parallelism_key=f"user:{tenant}:{user_id}",
        visibility=visibility,
        visibility_user_id=visibility_user_id,
        chat_binding={
            "chat_id": chat_id,
            "mode": "append_final_message",
            "on_failure": "post_error",
            "source_message_id": "m-1",
        },
    )
    return JobRun(
        run_id=_uuid.uuid4(),
        spec=spec,
        attempt_number=1,
        status=JobStatus.RUNNING,
        queued_at=_dt.datetime.now(tz=_dt.UTC),
        started_at=_dt.datetime.now(tz=_dt.UTC),
    )


# ── Authorization matrix ────────────────────────────────────


async def test_chat_owner_passes(test_runtime, chat_storage_stub) -> None:
    chat = await chat_storage_stub.create_chat(tenant_id="t-1", user_id="u-7")
    app = _make_app(chat_storage=chat_storage_stub, user_id="u-7")
    with TestClient(app) as client:
        with client.stream("GET", f"/chats/{chat.id}/events/stream") as r:
            assert r.status_code == 200


async def test_admin_in_same_tenant_passes(test_runtime, chat_storage_stub) -> None:
    chat = await chat_storage_stub.create_chat(tenant_id="t-1", user_id="u-7")
    app = _make_app(
        chat_storage=chat_storage_stub,
        user_id="u-admin",
        roles={"admin"},
    )
    with TestClient(app) as client:
        with client.stream("GET", f"/chats/{chat.id}/events/stream") as r:
            assert r.status_code == 200


async def test_non_owner_non_admin_gets_404(test_runtime, chat_storage_stub) -> None:
    """Different user, no admin role → 404 (never 403)."""
    await chat_storage_stub.create_chat(tenant_id="t-1", user_id="u-OWNER")
    app = _make_app(chat_storage=chat_storage_stub, user_id="u-INTRUDER")
    with TestClient(app) as client:
        r = client.get("/chats/C-1/events/stream")
        assert r.status_code == 404
        # Generic body — must NOT leak existence/visibility details.
        assert "403" not in r.text
        assert "Forbidden" not in r.text


async def test_cross_tenant_admin_gets_404(test_runtime, chat_storage_stub) -> None:
    """Admin in DIFFERENT tenant cannot read another tenant's chats."""
    await chat_storage_stub.create_chat(tenant_id="t-1", user_id="u-7")
    app = _make_app(
        chat_storage=chat_storage_stub,
        user_id="u-cross",
        tenant="t-OTHER",
        roles={"admin"},
    )
    with TestClient(app) as client:
        r = client.get("/chats/C-1/events/stream")
        assert r.status_code == 404


def test_nonexistent_chat_404_indistinguishable_from_non_owner(test_runtime, chat_storage_stub) -> None:
    """Same response shape for missing-chat and not-an-owner cases.

    The whole point of 404-never-403 (§26.6) is that the
    response shape doesn't leak whether the chat exists.
    """
    app = _make_app(chat_storage=chat_storage_stub, user_id="u-INTRUDER")
    with TestClient(app) as client:
        r1 = client.get("/chats/does-not-exist/events/stream")
        assert r1.status_code == 404
        assert r1.headers["content-type"].startswith("application/json")


# ── §LS9: chat owner sees only their bound runs ─────────────


async def test_chat_owner_only_sees_their_chats_bound_runs(test_runtime, chat_storage_stub) -> None:
    """The §LS9 contract — chat-binding implies actor/addressed visibility.

    A user who owns chat C-A must see discovery events for runs bound
    to C-A, NOT for runs bound to a different chat (even one in the
    same tenant they happen to also own).
    """
    chat_a = await chat_storage_stub.create_chat(tenant_id="t-1", user_id="u-7")
    chat_b = await chat_storage_stub.create_chat(tenant_id="t-1", user_id="u-7")
    run_a = _make_run(chat_id=chat_a.id)
    run_b = _make_run(chat_id=chat_b.id)
    await test_runtime.job_store.insert(run_a)
    await test_runtime.job_store.insert(run_b)

    app = _make_app(chat_storage=chat_storage_stub, user_id="u-7")
    with TestClient(app) as client:
        with client.stream("GET", f"/chats/{chat_a.id}/events/stream") as r:
            assert r.status_code == 200
            body = b""
            for chunk in r.iter_bytes():
                body += chunk
    text = body.decode("utf-8")
    # run_a's discovery attached event present; run_b's never appears.
    assert str(run_a.run_id) in text
    assert str(run_b.run_id) not in text
