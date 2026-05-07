"""Run inspection + control (§16.3).

| Method | Path | Purpose |
|---|---|---|
| ``GET``  | ``/runs``                     | List runs (visibility-filtered). |
| ``GET``  | ``/runs/{run_id}``            | Fetch one (404-not-403). |
| ``GET``  | ``/runs/{run_id}/stream``     | SSE of ``bloom.*`` events. |
| ``POST`` | ``/runs/{run_id}/cancel``     | Cancel a pending/running run. |
| ``POST`` | ``/runs/{run_id}/retry``      | Force a fresh attempt. |

The §26 visibility filter is enforced by
:func:`require_visible_run` on every per-id endpoint and by an
in-memory predicate on the list endpoint.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from orchid_ai.core.events.job import JobRun, JobStatus
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.events.visibility import run_is_visible

from ..auth import get_auth_context
from ..context import get_events_runtime
from ._visibility import require_visible_run

router = APIRouter(prefix="/runs", tags=["events"])
_logger = logging.getLogger(__name__)


@router.get("")
async def list_runs(
    status: str | None = Query(default=None),
    trigger_id: str | None = Query(default=None),
    since: str | None = Query(default=None, description="ISO8601"),
    limit: int = Query(default=100, ge=1, le=1000),
    auth: OrchidAuthContext = Depends(get_auth_context),
    events: Any = Depends(get_events_runtime),
) -> dict[str, Any]:
    """List runs visible to the caller (§26.5)."""
    since_dt = _parse_iso(since)
    rows = await events.job_store.list(
        trigger_id=trigger_id,
        status=status,
        since=since_dt,
        limit=limit,
    )
    visible = [r for r in rows if run_is_visible(r, auth)]
    return {"items": [_run_to_dict(r) for r in visible]}


@router.get("/{run_id}")
async def get_run(run: JobRun = Depends(require_visible_run)) -> dict[str, Any]:
    return _run_to_dict(run, include_result=True)


@router.get("/{run_id}/stream")
async def stream_run(
    run: JobRun = Depends(require_visible_run),
    events: Any = Depends(get_events_runtime),
) -> StreamingResponse:
    """SSE stream of ``bloom.*`` events for this run (§18).

    The stream closes after ``bloom.run.finished`` OR when the
    BloomEventStream's idle timeout elapses (default 5 minutes with
    no traffic).  Visibility is checked on connect via
    :func:`require_visible_run`; per §26.7 a run's visibility
    cannot widen mid-stream so the connect-time check is enough.
    """
    if events.event_stream is None:
        raise HTTPException(status_code=503, detail="event stream not configured")

    run_id = run.run_id

    async def _generator():
        async for event in events.event_stream.subscribe_run(run_id):
            yield _format_sse(event)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/cancel")
async def cancel_run(
    run: JobRun = Depends(require_visible_run),
    events: Any = Depends(get_events_runtime),
) -> dict[str, Any]:
    """Cancel a pending or running run.

    Cancellation in v1 is **best-effort**: we flip the row to
    ``cancelled`` so the operator UI reflects the intent, but a run
    already in flight inside the supervisor finishes naturally
    (LangGraph doesn't yet expose a per-run cancel hook the
    framework can call into).  The cancel still suppresses any
    pending retry — once cancelled the run is terminal.
    """
    if run.status in (
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    ):
        return _run_to_dict(run, include_result=True)

    run.status = JobStatus.CANCELLED
    run.finished_at = _dt.datetime.now(tz=_dt.UTC)
    run.error = "cancelled by operator"
    await events.job_store.update(run)
    return _run_to_dict(run, include_result=True)


@router.post("/{run_id}/retry")
async def retry_run(
    run: JobRun = Depends(require_visible_run),
    events: Any = Depends(get_events_runtime),
) -> dict[str, Any]:
    """Force a fresh retry attempt.

    Re-enqueues the originating signal so the processor picks a
    new ``JobRun`` row with ``attempt_number = previous + 1``.
    Returns the new ``queue_msg_id`` plus the previous run id for
    operator correlation.
    """
    queue_msg_id = await events.signal_queue.enqueue(run.spec.signal_id)
    return {
        "previous_run_id": str(run.run_id),
        "queue_msg_id": queue_msg_id,
        "retried_at": _dt.datetime.now(tz=_dt.UTC).isoformat(),
    }


# ── Helpers ─────────────────────────────────────────────────


def _run_to_dict(run: JobRun, *, include_result: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": str(run.run_id),
        "trigger_id": run.spec.trigger_id,
        "signal_id": str(run.spec.signal_id),
        "agent_name": run.spec.agent_name,
        "attempt_number": run.attempt_number,
        "status": run.status.value,
        "visibility": run.spec.visibility,
        "visibility_user_id": run.spec.visibility_user_id,
        "queued_at": run.queued_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error,
    }
    if include_result:
        payload["result"] = run.result
        payload["next_retry_at"] = run.next_retry_at.isoformat() if run.next_retry_at else None
    return payload


def _format_sse(event: Any) -> str:
    """Render a :class:`BloomEvent` in SSE wire format."""
    import json as _json

    data = _json.dumps(
        {
            "type": event.type,
            "run_id": str(event.run_id),
            "occurred_at": event.occurred_at.isoformat(),
            "payload": event.payload,
        }
    )
    return f"event: {event.type}\ndata: {data}\n\n"


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
