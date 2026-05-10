"""Triggers / jobs read surface (§16.2).

| Method | Path | Purpose |
|---|---|---|
| ``GET`` | ``/jobs`` | List trigger definitions (read-only in v1). |
| ``GET`` | ``/jobs/{trigger_id}/runs`` | List runs for a trigger. |

The "jobs" name follows the spec — a trigger plus its history is
what an operator inspects.  In v1 trigger CRUD is YAML-only, so
this router is pure read-side.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.events.visibility import run_is_visible

from ..auth import get_auth_context
from ..context import get_events_runtime

router = APIRouter(prefix="/jobs", tags=["events"])
_logger = logging.getLogger(__name__)


@router.get("")
async def list_jobs(
    auth: OrchidAuthContext = Depends(get_auth_context),
    events: Any = Depends(get_events_runtime),
) -> dict[str, Any]:
    """List every active trigger as a job summary.

    The trigger registry is the source of truth (versioned snapshots
    in the trigger store are for retry-replay, not for the API
    surface).  Visibility for triggers themselves: any authenticated
    user in the tenant sees the trigger metadata; the run rows are
    independently visibility-filtered by ``/jobs/{id}/runs``.
    """
    items: list[dict[str, Any]] = []
    for trigger in events.trigger_registry.all():
        items.append(
            {
                "trigger_id": trigger.trigger_id,
                "parallelism": getattr(trigger, "parallelism", "per_user"),
                "visibility": getattr(trigger, "visibility", "admin"),
                "respect_chat_binding": getattr(trigger, "respect_chat_binding", False),
            }
        )
    return {"items": items}


@router.get("/{trigger_id}/runs")
async def list_runs_for_trigger(
    trigger_id: str,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    auth: OrchidAuthContext = Depends(get_auth_context),
    events: Any = Depends(get_events_runtime),
) -> dict[str, Any]:
    """List runs for a specific trigger.

    Returns 404 (not 403) when the trigger doesn't exist OR when the
    caller can't see ANY of its runs — exactly the §26.6 contract.
    """
    trigger = events.trigger_registry.get(trigger_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail="trigger not found")

    rows = await events.job_store.list(trigger_id=trigger_id, status=status, limit=limit)
    visible = [r for r in rows if run_is_visible(r, auth)]
    if not visible and rows:
        # Trigger exists but caller can't see anything — still 404
        # to avoid leaking the trigger's existence to non-actors.
        # Admins always see everything, so we only hit this when the
        # caller has no actor / addressed match.
        if "admin" not in (auth.roles or frozenset()):
            raise HTTPException(status_code=404, detail="trigger not found")
    return {"items": [_run_to_dict(r) for r in visible]}


def _run_to_dict(run: Any) -> dict[str, Any]:
    return {
        "run_id": str(run.run_id),
        "trigger_id": run.spec.trigger_id,
        "signal_id": str(run.spec.signal_id),
        "agent_name": run.spec.agent_name,
        "attempt_number": run.attempt_number,
        "status": run.status.value,
        "visibility": run.spec.visibility,
        "queued_at": run.queued_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error,
    }
