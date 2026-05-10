"""Schedule list / inspect / toggle (§16.4).

| Method | Path | Purpose |
|---|---|---|
| ``GET``   | ``/schedules``       | List schedules (admin-only). |
| ``GET``   | ``/schedules/{id}``  | Fetch one. |
| ``PATCH`` | ``/schedules/{id}``  | Toggle ``enabled`` / change cron. |

Schedules are admin-only resources because they describe the
operational shape of the deployment (when do background jobs
fire?).  Tenant users have no business inspecting cron tables.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from orchid_ai.core.events.store import OrchidScheduleRecord
from orchid_ai.core.state import OrchidAuthContext

from ..auth import get_auth_context
from ..context import get_events_runtime

router = APIRouter(prefix="/schedules", tags=["events"])
_logger = logging.getLogger(__name__)


def _require_admin(auth: OrchidAuthContext) -> None:
    if "admin" not in (auth.roles or frozenset()):
        # 404, not 403 — schedules are admin-private (§26.6).
        raise HTTPException(status_code=404, detail="not found")


@router.get("")
async def list_schedules(
    auth: OrchidAuthContext = Depends(get_auth_context),
    events: Any = Depends(get_events_runtime),
) -> dict[str, Any]:
    _require_admin(auth)
    rows = list(await events.schedule_store.list())
    return {"items": [_to_dict(r) for r in rows]}


@router.get("/{schedule_id}")
async def get_schedule(
    schedule_id: str,
    auth: OrchidAuthContext = Depends(get_auth_context),
    events: Any = Depends(get_events_runtime),
) -> dict[str, Any]:
    _require_admin(auth)
    record = await events.schedule_store.get(schedule_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    return _to_dict(record)


class _SchedulePatchBody(BaseModel):
    enabled: bool | None = Field(default=None)
    cron: str | None = Field(default=None)
    interval_seconds: int | None = Field(default=None, gt=0)
    model_config = ConfigDict(extra="forbid")


@router.patch("/{schedule_id}")
async def patch_schedule(
    schedule_id: str,
    body: _SchedulePatchBody,
    auth: OrchidAuthContext = Depends(get_auth_context),
    events: Any = Depends(get_events_runtime),
) -> dict[str, Any]:
    _require_admin(auth)
    record = await events.schedule_store.get(schedule_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")

    # Build the patched record — note the ``cron`` ↔ ``interval_seconds``
    # exclusivity is preserved by patching one and clearing the other
    # only when the caller provides the new field.
    new_cron = body.cron if body.cron is not None else record.cron
    new_interval = body.interval_seconds if body.interval_seconds is not None else record.interval_seconds
    if body.cron is not None and body.interval_seconds is None:
        new_interval = None
    if body.interval_seconds is not None and body.cron is None:
        new_cron = None
    new_enabled = body.enabled if body.enabled is not None else record.enabled

    patched = OrchidScheduleRecord(
        schedule_id=record.schedule_id,
        trigger_id=record.trigger_id,
        cron=new_cron,
        interval_seconds=new_interval,
        identity_claim=record.identity_claim,
        last_fire_at=record.last_fire_at,
        next_fire_at=record.next_fire_at,
        enabled=new_enabled,
    )
    await events.schedule_store.upsert(patched)

    # Re-sync the live scheduler producer so the patched cadence
    # takes effect without a process restart.  The producer's
    # ``refresh`` is idempotent — calling it on a non-scheduler
    # producer (e.g. when the deployment doesn't run a scheduler at
    # all) is a no-op.
    for producer in events.producers:
        refresh = getattr(producer, "refresh", None)
        if refresh is not None:
            try:
                await refresh()
            except Exception:
                _logger.exception(
                    "schedule patch: producer %s refresh failed",
                    getattr(producer, "name", type(producer).__name__),
                )

    return _to_dict(patched)


# ── Helpers ─────────────────────────────────────────────────


def _to_dict(record: OrchidScheduleRecord) -> dict[str, Any]:
    return {
        "schedule_id": record.schedule_id,
        "trigger_id": record.trigger_id,
        "cron": record.cron,
        "interval_seconds": record.interval_seconds,
        "identity_claim": record.identity_claim,
        "last_fire_at": record.last_fire_at.isoformat() if record.last_fire_at else None,
        "next_fire_at": record.next_fire_at.isoformat() if record.next_fire_at else None,
        "enabled": record.enabled,
    }
