"""HTTP ingestion producer — FastAPI adapter for the Pollen signal layer.

Mounts a FastAPI sub-router that exposes::

    POST {mount}      ← default ``/signals``

The route validates ``X-Orchid-Source`` against an in-memory
:class:`~orchid_ai.events.ingestion.SignalSourceRegistry`, runs the
matched :class:`~orchid_ai.events.auth.base.SignalAuthValidator`, builds
a :class:`~orchid_ai.core.events.signal.SignalEnvelope`, calls
``dispatcher.ingest`` and returns ``202 + {signal_id, deduplicated}``.

This class lives in ``orchid-api`` (not the framework library) because
it depends on FastAPI, which is an orchid-api–specific dependency.
The domain types (:class:`~orchid_ai.events.ingestion.SignalSource`,
:class:`~orchid_ai.events.ingestion.SignalSourceRegistry`) and the
:func:`~orchid_ai.events.bootstrap.build_signal_source_registry` factory
are library-level concerns and remain in ``orchid_ai``.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request, status as _status
from fastapi.responses import JSONResponse

from orchid_ai.core.events.dispatcher import OrchidSignalDispatcher
from orchid_ai.core.events.errors import (
    SignalAuthValidationError,
    SignalSourceTypeNotAllowedError,
    SignalSourceUnknownError,
)
from orchid_ai.core.events.producer import OrchidSignalProducer
from orchid_ai.core.events.signal import SignalEnvelope
from orchid_ai.events.auth.base import SignalAuthRequest
from orchid_ai.events.ingestion import SignalSourceRegistry

if TYPE_CHECKING:
    pass

_logger = logging.getLogger(__name__)


class HTTPIngestionProducer(OrchidSignalProducer):
    """FastAPI-backed signal source.

    The dispatcher is set in :meth:`start` per the
    :class:`~orchid_ai.core.events.producer.OrchidSignalProducer`
    contract; the router is built eagerly so the orchid-api lifespan
    can mount it before ``start`` runs.

    Headers:

    - ``X-Orchid-Source`` — required.  Must match a registered source.
    - ``X-Orchid-Signature`` — consumed by the validator (HMAC).
    - ``Idempotency-Key`` — copied verbatim into ``dedupe_key`` so
      re-deliveries are deduplicated by the
      ``UNIQUE (source, dedupe_key)`` index on ``signals``.
    - ``Authorization`` — consumed by :class:`~orchid_ai.events.auth.bearer.BearerValidator`.

    Body (JSON):

    .. code-block:: json

        {
          "type": "support.ticket.created",
          "payload": {...},
          "occurred_at": "2026-05-07T08:51:42Z",
          "tenant_key": "acme-prod",
          "user_id": "u-7abc12",
          "correlation_id": "...",
          "identity_claim": {...},
          "chat_binding": {...}
        }

    Only ``type`` and ``tenant_key`` are required; the rest default
    to None / now.
    """

    def __init__(
        self,
        *,
        registry: SignalSourceRegistry,
        mount: str = "/signals",
        max_body_bytes: int = 1_000_000,
    ) -> None:
        self._registry = registry
        self._mount = mount.rstrip("/") or "/signals"
        self._max_body = max_body_bytes
        self._dispatcher: OrchidSignalDispatcher | None = None

        self._router = APIRouter()
        self._router.add_api_route(
            self._mount,
            self._ingest,
            methods=["POST"],
            status_code=_status.HTTP_202_ACCEPTED,
            response_class=JSONResponse,
            tags=["events"],
            include_in_schema=True,
        )

    # ── Lifecycle ────────────────────────────────────────

    @property
    def name(self) -> str:
        return "HTTPIngestionProducer"

    @property
    def router(self) -> APIRouter:
        return self._router

    @property
    def mount(self) -> str:
        return self._mount

    async def start(self, dispatcher: OrchidSignalDispatcher) -> None:
        self._dispatcher = dispatcher
        _logger.info(
            "[HTTPIngestionProducer] started — mount=%s sources=%d",
            self._mount,
            len(self._registry),
        )

    async def stop(self) -> None:
        # Stateless — the FastAPI lifespan tears the router down with
        # the app; nothing to release here.
        self._dispatcher = None

    # ── Handler ──────────────────────────────────────────

    async def _ingest(self, request: Request) -> JSONResponse:
        """The route body.  Wired into the FastAPI router at construction.

        Returns ``202 + {signal_id, deduplicated}`` on success, or
        a structured error otherwise:

        - 400 — malformed JSON / missing required field.
        - 401 — signature / bearer rejected by the validator.
        - 403 — declared signal ``type`` is not in the source's
          allow-list.
        - 404 — ``X-Orchid-Source`` not registered.
        - 413 — body exceeds ``max_body_bytes``.
        - 503 — dispatcher not yet started (lifespan ordering bug).
        """
        if self._dispatcher is None:
            raise HTTPException(503, "events dispatcher not started")

        source_id = request.headers.get("x-orchid-source", "")
        if not source_id:
            raise HTTPException(400, "missing X-Orchid-Source header")

        source = self._registry.get(source_id)
        if source is None:
            raise HTTPException(404, f"unknown source {source_id!r}")

        raw_body = await request.body()
        if len(raw_body) > self._max_body:
            raise HTTPException(413, "request body too large")

        # Validate before parsing — a bad signature should never
        # surface payload contents in the error response, and the
        # HMAC validator hashes the raw bytes.
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            await source.validator.validate(
                SignalAuthRequest(
                    source_id=source_id,
                    raw_body=raw_body,
                    headers=headers,
                )
            )
        except SignalAuthValidationError as exc:
            raise HTTPException(401, str(exc)) from exc

        # Body must be JSON.
        try:
            body = _json.loads(raw_body or b"{}")
        except Exception as exc:
            raise HTTPException(400, f"body is not valid JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be a JSON object")

        signal_type = body.get("type")
        tenant_key = body.get("tenant_key")
        if not isinstance(signal_type, str) or not signal_type:
            raise HTTPException(400, "missing required field 'type'")
        if not isinstance(tenant_key, str) or not tenant_key:
            raise HTTPException(400, "missing required field 'tenant_key'")

        if source.allowed_types and signal_type not in source.allowed_types:
            raise HTTPException(
                403,
                f"signal type {signal_type!r} is not allowed for source "
                f"{source_id!r} (allow-list: {sorted(source.allowed_types)})",
            )

        dedupe_key = request.headers.get("idempotency-key") or body.get("dedupe_key")

        envelope = SignalEnvelope(
            type=signal_type,
            payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
            source=source_id,
            occurred_at=_parse_occurred_at(body.get("occurred_at")),
            tenant_key=tenant_key,
            user_id=_str_or_none(body.get("user_id")),
            correlation_id=_str_or_none(body.get("correlation_id")),
            dedupe_key=dedupe_key if isinstance(dedupe_key, str) and dedupe_key else None,
            identity_claim=body.get("identity_claim") if isinstance(body.get("identity_claim"), dict) else None,
            chat_binding=body.get("chat_binding") if isinstance(body.get("chat_binding"), dict) else None,
        )

        try:
            result = await self._dispatcher.ingest(envelope)
        except SignalSourceUnknownError as exc:
            raise HTTPException(404, str(exc)) from exc
        except SignalSourceTypeNotAllowedError as exc:
            raise HTTPException(403, str(exc)) from exc

        return JSONResponse(
            status_code=_status.HTTP_202_ACCEPTED,
            content={
                "signal_id": str(result.signal_id),
                "deduplicated": result.deduplicated,
            },
        )


# ── Helpers ─────────────────────────────────────────────────


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _parse_occurred_at(value: Any) -> _dt.datetime:
    """Parse the ``occurred_at`` field — RFC 3339 / ISO 8601.

    Falls back to ``now()`` when missing or unparseable rather than
    rejecting; replayed historical events sometimes lack the field
    and the framework prefers to ingest with a best-effort timestamp
    over dropping the signal.
    """
    if isinstance(value, str) and value:
        try:
            iso = value.replace("Z", "+00:00")
            parsed = _dt.datetime.fromisoformat(iso)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_dt.UTC)
            return parsed
        except Exception:
            return _dt.datetime.now(tz=_dt.UTC)
    return _dt.datetime.now(tz=_dt.UTC)
