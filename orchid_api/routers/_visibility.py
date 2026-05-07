"""``require_visible_run`` — the §26.6 ``404-never-403`` gate.

Every per-id endpoint on ``/runs/{run_id}`` and ``/runs/{run_id}/stream``
goes through this dependency BEFORE its real body.  The same gate
covers ``/signals/{signal_id}``, with the additional rule that a
signal is visible iff at least one of its triggered runs is.

Two behaviours that the spec is explicit about:

- Same response code for ``does not exist`` and ``exists but not
  visible``.  Returning 403 would confirm the resource exists on a
  guessable UUID space and that's a small but real information leak.
- Cross-tenant access is **always** 404 regardless of role, even for
  admins.

The dependency uses :func:`orchid_ai.events.visibility.run_is_visible`
under the hood, which is the same predicate the §26.5 SQL filter
emits — keeping the API's read path consistent with the on-disk
visibility check.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from fastapi import Depends, HTTPException

from orchid_ai.core.events.job import JobRun
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.events.visibility import run_is_visible

from orchid_ai.persistence.base import OrchidChatStorage

from ..auth import get_auth_context
from ..context import get_chat_repo, get_events_runtime


async def require_visible_run(
    run_id: str,
    auth: OrchidAuthContext = Depends(get_auth_context),
    events: Any = Depends(get_events_runtime),
) -> JobRun:
    """Resolve ``run_id`` → :class:`JobRun` AND check visibility.

    Returns the :class:`JobRun` for the route handler to consume.
    Raises :class:`HTTPException(404)` for both missing and
    not-visible cases — never 403.
    """
    try:
        rid = _uuid.UUID(run_id)
    except ValueError as exc:
        # Same shape — don't leak whether a malformed UUID exists.
        raise HTTPException(status_code=404, detail="run not found") from exc

    run = await events.job_store.get(rid)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if not run_is_visible(run, auth):
        raise HTTPException(status_code=404, detail="run not found")
    return run


async def require_chat_owner_or_admin(
    chat_id: str,
    auth: OrchidAuthContext = Depends(get_auth_context),
    chat_repo: OrchidChatStorage = Depends(get_chat_repo),
) -> OrchidAuthContext:
    """Allow only the chat owner (or an admin in the same tenant).

    Used by ``GET /chats/{chat_id}/events/stream`` (Phase 4.5 §LS6).
    Mirrors the §26.6 ``404-never-403`` contract:

    - Chat doesn't exist                          → 404
    - Different tenant                            → 404 (admins included)
    - Same tenant but not owner and not admin     → 404 (no info leak)
    - Same tenant, owner OR admin                 → returns the auth

    Returning the auth (not the chat) keeps the dependency tiny and
    lets the route pull the chat fresh if it needs it.
    """
    chat = await chat_repo.get_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    # Cross-tenant — always 404, regardless of role.
    if chat.tenant_id != auth.tenant_key:
        raise HTTPException(status_code=404, detail="chat not found")
    # Owner OR admin in the same tenant.
    if chat.user_id == auth.user_id:
        return auth
    if "admin" in (auth.roles or frozenset()):
        return auth
    raise HTTPException(status_code=404, detail="chat not found")


async def require_visible_signal(
    signal_id: str,
    auth: OrchidAuthContext = Depends(get_auth_context),
    events: Any = Depends(get_events_runtime),
) -> Any:
    """``GET /signals/{id}`` analogue.

    A signal is visible iff at least one of its triggered runs is
    visible to the caller.  When NO runs exist (signal hasn't fired
    yet), we fall back to the identity claim's flavour: signals
    bound to ``act_as_user`` / ``addressed_to_user`` are visible to
    that user; ``service_account`` signals are admin-only.
    """
    try:
        sid = _uuid.UUID(signal_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="signal not found") from exc

    signal = await events.signal_store.get(sid)
    if signal is None:
        raise HTTPException(status_code=404, detail="signal not found")

    # Cross-tenant short-circuit (admins included — §26.6).
    if signal.tenant_key != auth.tenant_key:
        raise HTTPException(status_code=404, detail="signal not found")

    # Admins see every same-tenant signal.
    if "admin" in (auth.roles or frozenset()):
        return signal

    # Look at the triggered runs — visible iff at least one matches.
    runs = await events.job_store.list(limit=200)
    matching_runs = [r for r in runs if r.spec.signal_id == sid]
    if matching_runs and any(run_is_visible(r, auth) for r in matching_runs):
        return signal

    # No runs yet — fall back to identity-claim flavour.
    claim = signal.identity_claim or {}
    mode = claim.get("mode")
    if mode in ("act_as_user", "addressed_to_user"):
        # Visible to the named user-of-record (signal.user_id is the
        # canonical writer in HTTP ingestion; producers may also
        # inline the user_id via ``identity.user_id``).
        target = signal.user_id or claim.get("user_id")
        if target == auth.user_id:
            return signal
    # service_account or empty — admin-only, and we already handled admin.
    raise HTTPException(status_code=404, detail="signal not found")
