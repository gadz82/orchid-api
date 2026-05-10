"""``GET /chats/{chat_id}/events/stream`` — chat-channel SSE
(Phase 4.5 §LS6 + §LS7).

A long-lived SSE endpoint that streams ``chat.bloom.attached`` /
``chat.bloom.tick`` / ``chat.bloom.finished`` events for every Bloom
that's currently bound to this chat.  Distinct from the per-message
``POST /chats/{id}/messages/stream`` endpoint:

- ``messages/stream`` is per-request, terminates with the response.
- ``events/stream`` is per-chat-session, lives for as long as the
  user keeps the chat open.

Authorization: :func:`require_chat_owner_or_admin` enforces the
§26.6 ``404-never-403`` contract — non-owners and cross-tenant
callers see the same 404 as a non-existent chat.

Discovery vs. live:

1. On connect, the endpoint queries
   ``OrchidJobStore.list(chat_binding_chat_id=..., statuses=[PENDING, RUNNING])``
   and synthesises one ``chat.bloom.attached`` event per in-flight
   bound run.  This guarantees that a user reconnecting mid-Bloom
   sees the same ``attached`` event the original connection saw —
   the frontend reconstructs progress-card state from the discovery
   pass, no cursor / since-id semantics needed (LS10).
2. Then it subscribes to the in-process ``chat:{chat_id}`` channel
   and forwards every event the dual-publish branch lands.

The stream closes after the channel's idle timeout (default 5
minutes with no traffic) — clients reconnect with the same
``chat_id`` to refresh discovery.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from orchid_ai.core.events.job import JobRun, JobStatus
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.events.streaming import ChatBloomEvent

from ..context import get_events_runtime
from ._visibility import require_chat_owner_or_admin

router = APIRouter(prefix="/chats", tags=["chat-events"])
_logger = logging.getLogger(__name__)


@router.get("/{chat_id}/events/stream")
async def stream_chat_events(
    chat_id: str,
    auth: OrchidAuthContext = Depends(require_chat_owner_or_admin),
    events: Any = Depends(get_events_runtime),
) -> StreamingResponse:
    """Stream ``chat.bloom.*`` events for ``chat_id`` (Phase 4.5 §LS6/LS7).

    Discovery → subscribe pattern.  See module docstring.
    """
    if events.event_stream is None:
        raise HTTPException(status_code=503, detail="event stream not configured")

    async def _generator():
        # 1. Discovery.  Use ``list_runs`` with the new
        # ``chat_binding_chat_id`` filter and ``statuses=[PENDING, RUNNING]``.
        in_flight = await events.job_store.list(
            chat_binding_chat_id=chat_id,
            statuses=[JobStatus.PENDING.value, JobStatus.RUNNING.value],
            limit=200,
        )
        for run in in_flight:
            synthetic = _synthetic_attached_for(run)
            yield _format_chat_sse(synthetic)

        # 2. Subscribe to the live chat channel.
        async for event in events.event_stream.subscribe(f"chat:{chat_id}"):
            yield _format_chat_sse(event)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


# ── Helpers ─────────────────────────────────────────────────


def _synthetic_attached_for(run: JobRun) -> ChatBloomEvent:
    """Build a ``chat.bloom.attached`` event from a :class:`JobRun` row.

    Used only on the discovery path so a reconnecting user sees the
    same ``attached`` event the original connection saw, even
    though the original ``bloom.run.queued`` / ``bloom.run.started``
    events were ephemeral.

    The synthetic event MUST be dropped by the frontend's run-id
    dedup if a real ``chat.bloom.attached`` arrives moments later
    on the live subscription — that's by design (LS10).
    """
    binding = run.spec.chat_binding or {}
    chat_id = binding.get("chat_id")
    occurred = run.started_at or run.queued_at or _dt.datetime.now(tz=_dt.UTC)
    identity_claim = run.spec.identity_claim or {}
    payload: dict[str, Any] = {
        "run_id": str(run.run_id),
        "trigger_id": run.spec.trigger_id,
        "agent_name": run.spec.agent_name,
        "source_message_id": binding.get("source_message_id"),
        "identity_mode": identity_claim.get("mode"),
        "attached_at": occurred.isoformat(),
    }
    return ChatBloomEvent(
        type="chat.bloom.attached",
        chat_id=chat_id or "",
        run_id=run.run_id,
        occurred_at=occurred,
        payload=payload,
    )


def _format_chat_sse(event: ChatBloomEvent) -> str:
    """Render a :class:`ChatBloomEvent` in SSE wire format."""
    data = json.dumps(
        {
            "type": event.type,
            "chat_id": event.chat_id,
            "run_id": str(event.run_id),
            "occurred_at": event.occurred_at.isoformat(),
            "payload": event.payload,
        }
    )
    return f"event: {event.type}\ndata: {data}\n\n"
