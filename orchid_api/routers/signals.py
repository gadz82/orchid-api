"""Signals CRUD + replay (§16.1).

| Method | Path | Purpose |
|---|---|---|
| ``GET``  | ``/signals``           | List signals (admin-only). |
| ``GET``  | ``/signals/{id}``      | Fetch one (visibility-filtered). |
| ``POST`` | ``/signals/{id}/replay`` | Re-enqueue (admin-only). |

The ``POST /signals`` ingest path lives elsewhere — it's served by
the :class:`HTTPIngestionProducer`'s router that the lifespan mounts
when ``events.enabled: true``.  This router covers only the read +
replay surface.
"""

from __future__ import annotations

import datetime as _dt
import logging
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from orchid_ai.core.state import OrchidAuthContext

from ..auth import get_auth_context
from ..context import get_events_runtime
from ._visibility import require_visible_signal

router = APIRouter(prefix="/signals", tags=["events"])
_logger = logging.getLogger(__name__)


def _require_admin(auth: OrchidAuthContext) -> None:
    """Per §26.5/§26.8 — admin-only endpoints return 404 (not 403)
    to non-admins to avoid leaking endpoint existence."""
    if "admin" not in (auth.roles or frozenset()):
        raise HTTPException(status_code=404, detail="not found")


@router.get("")
async def list_signals(
    type: str | None = Query(default=None),
    source: str | None = Query(default=None),
    since: str | None = Query(default=None, description="ISO8601 timestamp"),
    limit: int = Query(default=100, ge=1, le=1000),
    auth: OrchidAuthContext = Depends(get_auth_context),
    events: Any = Depends(get_events_runtime),
) -> dict[str, Any]:
    """List signals.  Admin-only per §16.1 (signals carry payloads
    that may have been admin-routed)."""
    _require_admin(auth)
    since_dt = _parse_iso(since)
    rows = await events.signal_store.list(
        type=type,
        tenant_key=auth.tenant_key,
        since=since_dt,
        limit=limit,
    )
    if source is not None:
        rows = [r for r in rows if r.source == source]
    return {"items": [_signal_to_dict(s) for s in rows]}


@router.get("/{signal_id}")
async def get_signal(
    signal=Depends(require_visible_signal),
) -> dict[str, Any]:
    return _signal_to_dict(signal)


@router.post("/{signal_id}/replay")
async def replay_signal(
    signal_id: str,
    auth: OrchidAuthContext = Depends(get_auth_context),
    events: Any = Depends(get_events_runtime),
) -> dict[str, Any]:
    """Re-enqueue a previously-ingested signal — admin-only per
    §26.8.  Returns the original ``signal_id`` and a fresh
    ``queue_msg_id``.

    Replays do NOT mint a new ``signals`` row; they push the
    existing row back onto the queue.  Idempotency follows the
    queue's normal contract, NOT
    ``UNIQUE (source, dedupe_key)`` — replays explicitly bypass
    that to allow re-running missed work.
    """
    _require_admin(auth)
    try:
        sid = _uuid.UUID(signal_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc

    signal = await events.signal_store.get(sid)
    if signal is None or signal.tenant_key != auth.tenant_key:
        raise HTTPException(status_code=404, detail="not found")

    queue_msg_id = await events.signal_queue.enqueue(signal.signal_id)
    return {
        "signal_id": str(signal.signal_id),
        "queue_msg_id": queue_msg_id,
        "replayed_at": _dt.datetime.now(tz=_dt.UTC).isoformat(),
    }


# ── Helpers ─────────────────────────────────────────────────


def _signal_to_dict(s: Any) -> dict[str, Any]:
    return {
        "signal_id": str(s.signal_id),
        "type": s.type,
        "source": s.source,
        "payload": s.payload,
        "tenant_key": s.tenant_key,
        "user_id": s.user_id,
        "correlation_id": s.correlation_id,
        "dedupe_key": s.dedupe_key,
        "identity_claim": s.identity_claim,
        "chat_binding": s.chat_binding,
        "occurred_at": s.occurred_at.isoformat(),
        "persisted_at": s.persisted_at.isoformat(),
        "relay_status": s.relay_status,
    }


def _parse_iso(value: str | None) -> _dt.datetime | None:
    if value is None:
        return None
    try:
        iso = value.replace("Z", "+00:00")
        parsed = _dt.datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.UTC)
        return parsed
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid 'since' timestamp: {value!r}") from exc
