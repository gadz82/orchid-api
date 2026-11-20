"""HTTP pipeline API: list, inspect, validate, run and stream.

The router is deliberately thin over the facade: auth, role, rate limit,
input-shape validation and error mapping live here; the pipeline runner owns
execution, graph limits and interpretation. No path here creates or writes a
chat row.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from orchid_ai.config.schema_events import OrchidEventsConfig
from orchid_ai.core.pipeline import (
    OrchidPipelineAuthError,
    OrchidPipelineDisabledError,
    OrchidPipelineError,
    OrchidPipelineInputError,
    OrchidPipelineNotFoundError,
    OrchidPipelineShapeError,
    OrchidPipelineTimeoutError,
)
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.pipeline.launcher import PipelineLauncher
from orchid_ai.pipeline.registry import OrchidPipelineInfo
from pydantic import ValidationError

from ..auth import get_auth_context
from ..context import app_ctx, get_pipelines
from ..models import (
    PipelineAsyncResponse,
    PipelineInfoOut,
    PipelineListResponse,
    PipelineRunRequest,
    PipelineRunResponse,
    PipelineValidateRequest,
    PipelineValidateResponse,
)
from ..rate_limit import TokenBucketLimiter
from ..settings import Settings, get_settings
from ._streaming import sse_event

logger = logging.getLogger(__name__)

# ORCHID-MEM: 01a1f3ce-65e0-7938-838e-eba835ee4522 — HTTP pipeline API; list/detail/validate/sync/async/SSE with stable errors, auth, role and rate limits.
router = APIRouter(prefix="/pipelines", tags=["pipelines"])

_SSE_HEARTBEAT_SECONDS = 15.0
_MAX_DETAIL_ERRORS = 20
_MAX_IDEMPOTENCY_KEY_CHARS = 200


class PipelineAPIError(Exception):
    """Stable pipeline error rendered by :func:`pipeline_error_handler`."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}

    def body(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


async def pipeline_error_handler(request: Request, exc: PipelineAPIError) -> JSONResponse:
    """Render a pipeline error as ``{"error": {code, message, details}}``."""
    return JSONResponse(status_code=exc.status_code, content={"error": exc.body()}, headers=exc.headers)


# ── Auth, role and rate limit ──────────────────────────────


async def require_pipeline_auth(
    authorization: str | None = Header(default=None),
    x_auth_domain: str | None = Header(default=None, alias="x-auth-domain"),
    settings: Settings = Depends(get_settings),
) -> OrchidAuthContext:
    """Resolve the caller, mapping auth failures to pipeline error codes."""
    if (not authorization or not authorization.startswith("Bearer ")) and not settings.dev_auth_bypass:
        raise PipelineAPIError(401, "pipeline_unauthorized", "Missing or invalid Bearer token")
    try:
        return await get_auth_context(
            authorization=authorization or "",
            x_auth_domain=x_auth_domain,
            settings=settings,
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            raise PipelineAPIError(403, "pipeline_forbidden", "Forbidden") from exc
        if exc.status_code == 401:
            raise PipelineAPIError(401, "pipeline_unauthorized", "Authentication failed") from exc
        raise PipelineAPIError(503, "pipeline_unavailable", "Identity resolver unavailable") from exc


async def require_pipeline_run_auth(
    auth: OrchidAuthContext = Depends(require_pipeline_auth),
    settings: Settings = Depends(get_settings),
) -> OrchidAuthContext:
    """Apply the per-user pipeline rate limit and return the caller."""
    limit = int(settings.pipeline_rate_limit_per_minute)
    if limit <= 0:
        return auth
    if app_ctx.pipeline_limiter is None or app_ctx.pipeline_limiter_limit != limit:
        app_ctx.pipeline_limiter = TokenBucketLimiter(capacity=limit, refill_per_second=limit / 60.0)
        app_ctx.pipeline_limiter_limit = limit
    allowed, retry_after = await app_ctx.pipeline_limiter.acquire(f"{auth.tenant_key}:{auth.user_id}")
    if not allowed:
        seconds = max(1, int(retry_after) + 1)
        raise PipelineAPIError(
            429,
            "pipeline_rate_limited",
            f"Pipeline rate limit exceeded; retry in ~{seconds}s",
            headers={"Retry-After": str(seconds)},
        )
    return auth


def _request_body(model: type) -> dict[str, Any]:
    """OpenAPI requestBody for handlers that parse raw JSON for stable 400s."""
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
    }


def _get_orchid() -> Any:
    if app_ctx.orchid is None:
        raise PipelineAPIError(503, "pipeline_unavailable", "Orchid is not initialised")
    return app_ctx.orchid


def _build_launcher(registry: Any, orchid: Any) -> PipelineLauncher:
    config = getattr(orchid, "config", None)
    events_config = getattr(config, "events", None)
    if not isinstance(events_config, OrchidEventsConfig):
        events_config = None
    return PipelineLauncher(
        registry=registry,
        events_config=events_config,
        signal_emitter_getter=lambda: getattr(app_ctx.events, "signal_emitter", None),
    )


# ── Body and input validation ──────────────────────────────


async def _read_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise PipelineAPIError(400, "pipeline_input_invalid", "Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise PipelineAPIError(400, "pipeline_input_invalid", "Request body must be a JSON object")
    return payload


def _model_errors(exc: ValidationError) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for raw in exc.errors()[:_MAX_DETAIL_ERRORS]:
        path = ".".join(str(part) for part in raw.get("loc", ()))
        errors.append(
            {
                "path": path,
                "type": str(raw.get("type", "value_error")),
                "message": str(raw.get("msg", "invalid value")),
            }
        )
    return errors


def _validate_model(model: type, payload: dict[str, Any]) -> Any:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise PipelineAPIError(
            422,
            "pipeline_input_schema_invalid",
            "Invalid pipeline request",
            details={"errors": _model_errors(exc)},
        ) from exc


def _prepare_input(definition: Any, raw: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw)
    input_config = getattr(definition.definition, "input", None)
    if input_config is not None:
        for key, value in getattr(input_config, "defaults", {}).items():
            payload.setdefault(key, value)
    shape = definition.input_shape
    if shape is None:
        return payload
    try:
        return shape.dump(payload, shape_version=shape.version)
    except OrchidPipelineShapeError as exc:
        raise PipelineAPIError(
            422,
            "pipeline_input_schema_invalid",
            str(exc),
            details={
                "shape": getattr(exc, "shape", ""),
                "errors": list(getattr(exc, "errors", ()))[:_MAX_DETAIL_ERRORS],
            },
        ) from exc


def _require_role(definition: Any, auth: OrchidAuthContext) -> None:
    role = definition.definition.expose.require_role
    if role and role not in (getattr(auth, "roles", None) or ()):
        raise PipelineAPIError(403, "pipeline_forbidden", f"Pipeline requires role {role!r}")


def _resolve(registry: Any, name: str) -> Any:
    definition = registry.get(name)
    if definition is None:
        raise PipelineAPIError(404, "pipeline_not_found", f"Pipeline {name!r} is not configured")
    return definition


def _public_info(info: OrchidPipelineInfo, definition: Any | None = None) -> dict[str, Any]:
    expose = info.expose
    async_config = info.async_
    expose_config = definition.definition.expose if definition is not None else None

    def _exposed(key: str, default: Any = None) -> Any:
        if expose_config is not None:
            return getattr(expose_config, key, default)
        return expose.get(key, default)

    return {
        "name": info.name,
        "description": info.description,
        "version": info.version,
        "jobs": [job.to_dict() for job in info.jobs],
        "input_schema": info.input_schema,
        "output_schema": info.output_schema,
        "expose": {
            "as_tool": bool(_exposed("as_tool", False)),
            "requires_approval": bool(_exposed("requires_approval", False)),
            "parallel_safe": bool(_exposed("parallel_safe", False)),
            "require_role": _exposed("require_role"),
            "max_tool_result_chars": _exposed("max_tool_result_chars"),
            "interpretation": _exposed("interpretation"),
        },
        "async": {
            "enabled": bool(async_config.get("enabled")),
            "mode": async_config.get("mode"),
        },
    }


# ── Endpoints ─────────────────────────────────────────────


@router.get("", response_model=PipelineListResponse)
async def list_pipelines(
    limit: int = Query(default=100, ge=1),
    offset: int = Query(default=0, ge=0),
    auth: OrchidAuthContext = Depends(require_pipeline_auth),
    registry: Any = Depends(get_pipelines),
) -> dict[str, Any]:
    infos = registry.list()
    total = len(infos)
    effective_limit = min(limit, 200)
    page = infos[offset : offset + effective_limit]
    return {
        "items": [_public_info(info, registry.get(info.name)) for info in page],
        "total": total,
        "limit": effective_limit,
        "offset": offset,
    }


@router.get("/{name}", response_model=PipelineInfoOut)
async def get_pipeline(
    name: str,
    auth: OrchidAuthContext = Depends(require_pipeline_auth),
    registry: Any = Depends(get_pipelines),
) -> dict[str, Any]:
    definition = _resolve(registry, name)
    info = registry.info(name)
    if info is None:
        raise PipelineAPIError(404, "pipeline_not_found", f"Pipeline {name!r} is not configured")
    return _public_info(info, definition)


@router.post(
    "/{name}/validate",
    response_model=PipelineValidateResponse,
    openapi_extra=_request_body(PipelineValidateRequest),
)
async def validate_pipeline_input(
    name: str,
    request: Request,
    auth: OrchidAuthContext = Depends(require_pipeline_auth),
    registry: Any = Depends(get_pipelines),
) -> dict[str, Any]:
    definition = _resolve(registry, name)
    _require_role(definition, auth)
    raw = await _read_body(request)
    if not isinstance(raw.get("input"), dict):
        return {
            "valid": False,
            "errors": [{"path": "input", "type": "value_error", "message": "input must be an object"}],
        }
    try:
        body = PipelineValidateRequest.model_validate(raw)
    except ValidationError as exc:
        return {"valid": False, "errors": _model_errors(exc)}
    try:
        _prepare_input(definition, body.input)
    except PipelineAPIError as exc:
        if exc.status_code == 422:
            return {"valid": False, "errors": exc.details.get("errors", []) if exc.details else []}
        raise
    return {"valid": True, "errors": []}


@router.post(
    "/{name}/runs",
    status_code=200,
    responses={
        200: {"model": PipelineRunResponse},
        202: {"model": PipelineAsyncResponse},
    },
    openapi_extra=_request_body(PipelineRunRequest),
)
async def run_pipeline(
    name: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: OrchidAuthContext = Depends(require_pipeline_run_auth),
    registry: Any = Depends(get_pipelines),
    orchid: Any = Depends(_get_orchid),
    settings: Settings = Depends(get_settings),
) -> Any:
    try:
        raw = await _read_body(request)
        if not isinstance(raw.get("input"), dict):
            raise PipelineAPIError(400, "pipeline_input_invalid", "input must be a JSON object")
        body = _validate_model(PipelineRunRequest, raw)
        if idempotency_key and len(idempotency_key) > _MAX_IDEMPOTENCY_KEY_CHARS:
            raise PipelineAPIError(400, "pipeline_input_invalid", "Idempotency-Key is too long")
        key = body.idempotency_key or idempotency_key
        if body.mode == "sync" and key:
            raise PipelineAPIError(
                400,
                "pipeline_input_invalid",
                "Idempotency-Key is only supported for async runs",
            )
        definition = _resolve(registry, name)
        _require_role(definition, auth)
        prepared = _prepare_input(definition, body.input)
        if body.mode == "async":
            if body.seed or body.include_graph or body.timeout_seconds is not None:
                raise PipelineAPIError(
                    400,
                    "pipeline_input_invalid",
                    "seed, include_graph and timeout_seconds are sync-only for async runs",
                )
            return await _run_async(
                name,
                definition,
                prepared,
                auth=auth,
                launcher=_build_launcher(registry, orchid),
                idempotency_key=key,
                timeout_seconds=body.timeout_seconds,
            )
        return await _run_sync(
            name,
            definition,
            prepared,
            orchid=orchid,
            auth=auth,
            seed=body.seed,
            include_graph=body.include_graph,
            timeout_seconds=body.timeout_seconds,
            settings=settings,
        )
    except PipelineAPIError:
        raise
    except Exception:
        logger.exception("[Pipelines] unexpected run failure for %s", name)
        raise PipelineAPIError(500, "pipeline_internal_error", f"pipeline {name!r} failed")


@router.post("/{name}/runs/stream", openapi_extra=_request_body(PipelineRunRequest))
async def stream_pipeline_run(
    name: str,
    request: Request,
    auth: OrchidAuthContext = Depends(require_pipeline_run_auth),
    registry: Any = Depends(get_pipelines),
    orchid: Any = Depends(_get_orchid),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    raw = await _read_body(request)
    if not isinstance(raw.get("input"), dict):
        raise PipelineAPIError(400, "pipeline_input_invalid", "input must be a JSON object")
    body = _validate_model(PipelineRunRequest, raw)
    if body.mode != "sync":
        raise PipelineAPIError(400, "pipeline_input_invalid", "Streaming requires mode='sync'")
    definition = _resolve(registry, name)
    _require_role(definition, auth)
    prepared = _prepare_input(definition, body.input)
    timeout = _effective_timeout(definition, body.timeout_seconds, settings)
    try:
        iterator = orchid.stream_pipeline(
            name,
            prepared,
            auth=auth,
            seed=body.seed,
            mode="direct",
            timeout=timeout,
        )
    except Exception:
        logger.exception("[Pipelines] could not start stream for %s", name)
        raise PipelineAPIError(500, "pipeline_internal_error", f"pipeline {name!r} stream failed")
    return StreamingResponse(
        _stream_frames(iterator, request, name),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_frames(iterator: Any, request: Request, name: str) -> AsyncIterator[str]:
    """Forward pipeline events as SSE frames with a keepalive and cleanup."""
    next_task = asyncio.create_task(anext(iterator))
    try:
        while True:
            if await request.is_disconnected():
                break
            done, _ = await asyncio.wait({next_task}, timeout=_SSE_HEARTBEAT_SECONDS)
            if next_task not in done:
                yield ": keepalive\n\n"
                continue
            try:
                event = next_task.result()
            except StopAsyncIteration:
                break
            except OrchidPipelineTimeoutError as exc:
                yield sse_event({"type": "pipeline.error", "error": {"code": "pipeline_timeout", "message": str(exc)}})
                break
            except Exception as exc:
                logger.exception("[Pipelines] stream failed for %s", name)
                yield sse_event(
                    {
                        "type": "pipeline.error",
                        "error": {
                            "code": "pipeline_internal_error",
                            "message": f"pipeline {name!r} stream failed ({type(exc).__name__})",
                        },
                    }
                )
                break
            yield sse_event(event.to_dict())
            if event.type == "pipeline.finished":
                break
            next_task = asyncio.create_task(anext(iterator))
    finally:
        if not next_task.done():
            next_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await next_task
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


# ── Run helpers ───────────────────────────────────────────


def _effective_timeout(definition: Any, requested: float | None, settings: Settings) -> float:
    configured = max(0.1, float(requested if requested is not None else settings.pipeline_run_timeout_seconds))
    limit = definition.limits.timeout_seconds
    if limit is not None:
        configured = min(configured, max(0.1, float(limit)))
    return configured


async def _run_sync(
    name: str,
    definition: Any,
    prepared: dict[str, Any],
    *,
    orchid: Any,
    auth: OrchidAuthContext,
    seed: list[dict[str, Any]],
    include_graph: bool,
    timeout_seconds: float | None,
    settings: Settings,
) -> dict[str, Any]:
    timeout = _effective_timeout(definition, timeout_seconds, settings)
    started = time.monotonic()
    try:
        result = await orchid.run_pipeline(
            name,
            prepared,
            auth=auth,
            seed=seed,
            mode="direct",
            timeout=timeout,
            include_graph=include_graph,
        )
    except OrchidPipelineTimeoutError as exc:
        raise PipelineAPIError(504, "pipeline_timeout", str(exc)) from exc
    except OrchidPipelineNotFoundError as exc:
        raise PipelineAPIError(404, "pipeline_not_found", str(exc)) from exc
    except (OrchidPipelineInputError, OrchidPipelineShapeError) as exc:
        raise PipelineAPIError(422, "pipeline_input_schema_invalid", str(exc)) from exc
    except OrchidPipelineDisabledError as exc:
        raise PipelineAPIError(404, "pipeline_not_found", str(exc)) from exc
    except OrchidPipelineAuthError as exc:
        raise PipelineAPIError(403, "pipeline_forbidden", str(exc)) from exc
    except OrchidPipelineError as exc:
        logger.exception("[Pipelines] run failed for %s", name)
        raise PipelineAPIError(500, "pipeline_internal_error", f"pipeline {name!r} failed") from exc

    elapsed = time.monotonic() - started
    if result.status in ("failed", "cancelled") and _is_timeout_result(result, elapsed, timeout):
        raise PipelineAPIError(504, "pipeline_timeout", result.error or "pipeline timed out")
    if include_graph and (result.metadata or {}).get("graph_omitted") == "size":
        raise PipelineAPIError(413, "pipeline_graph_too_large", "Requested graph exceeds the pipeline graph cap")
    return result.to_dict(include_graph=include_graph)


async def _run_async(
    name: str,
    definition: Any,
    prepared: dict[str, Any],
    *,
    auth: OrchidAuthContext,
    launcher: PipelineLauncher,
    idempotency_key: str | None,
    timeout_seconds: float | None,
) -> JSONResponse:
    dedupe_key = f"{auth.tenant_key}:{auth.user_id}:{idempotency_key}" if idempotency_key else None
    try:
        launched = await launcher.launch(
            name,
            prepared,
            auth=auth,
            chat_binding=None,
            correlation_id=None,
            timeout_seconds=timeout_seconds,
            dedupe_key=dedupe_key,
        )
    except Exception:
        logger.exception("[Pipelines] async launch failed for %s", name)
        raise PipelineAPIError(500, "pipeline_internal_error", f"pipeline {name!r} launch failed")
    if launched.status != "accepted":
        error = launched.error or {"code": "pipeline_async_unavailable", "message": "async is unavailable"}
        code = error.get("code", "pipeline_async_unavailable")
        status_code = {
            "pipeline_input_error": 400,
            "pipeline_auth_error": 403,
            "pipeline_async_emit_failed": 503,
        }.get(code, 409)
        raise PipelineAPIError(status_code, code, error.get("message", "async is unavailable"))
    trigger_id = definition.definition.async_.trigger_id
    status_url = f"/jobs/{quote(trigger_id or '', safe='')}/runs"
    return JSONResponse(
        status_code=202,
        content={
            "run_id": None,
            "signal_id": launched.signal_id,
            "pipeline": name,
            "status": "accepted",
            "deduplicated": launched.deduplicated,
            "status_url": status_url,
        },
    )


def _is_timeout_result(result: Any, elapsed: float, timeout: float) -> bool:
    """Classify a run-deadline timeout without confusing it with a job timeout."""
    metadata = result.metadata or {}
    if metadata.get("error_code") == "pipeline_timeout" or metadata.get("timed_out") is True:
        return True
    jobs = result.jobs or {}
    for job in jobs.values():
        if isinstance(job, dict) and job.get("status") == "failed":
            # A failed job is an executed failure, even if its message mentions
            # a timeout; the run result is authoritative.
            return False
    return elapsed >= float(timeout) * 0.9
