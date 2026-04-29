"""SSE streaming endpoint for real-time agent responses.

Streams tokens from the supervisor synthesis step as Server-Sent Events.
The graph driver, token buffer, and persistence live in
:mod:`._streaming`; this module owns only the HTTP adapter — auth,
request prep, and wiring the response.

SSE event format:
    data: {"type":"token","content":"Hello"}\\n\\n
    data: {"type":"status","agent":"menu","status":"started"}\\n\\n
    data: {"type":"done","response":"...","agents_used":[...],"auth_required":[...]}\\n\\n
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from orchid_ai.config.schema import OrchidAgentsConfig
from orchid_ai.core.mcp import OrchidMCPTokenStore
from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.observability import OrchidMetricsHandler
from orchid_ai.persistence.base import OrchidChatStorage
from orchid_ai.runtime import OrchidRuntime

from ..auth import get_auth_context
from ..context import (
    get_agents_config_optional,
    get_chat_repo,
    get_graph,
    get_mcp_token_store_optional,
    get_runtime,
)
from ..rate_limit import rate_limit
from ..settings import Settings, get_settings
from ._helpers import prepare_graph_state
from ._streaming import stream_supervisor_tokens

_settings = get_settings()
_stream_rate_limit = rate_limit(
    "messages",
    calls=_settings.rate_limit_messages_per_minute,
    period=60.0,
)

logger = logging.getLogger(__name__)
perf_logger = logging.getLogger("orchid.perf")

router = APIRouter(prefix="/chats", tags=["streaming"])


@router.get("/capabilities")
async def get_capabilities(
    agents_config: OrchidAgentsConfig | None = Depends(get_agents_config_optional),
):
    """Return server capabilities so the frontend can detect streaming support.

    Reads ``agents_config`` cached at startup rather than re-parsing
    ``agents.yaml`` on every call.
    """
    streaming = agents_config.supervisor.streaming_enabled if agents_config is not None else False
    return {"streaming_enabled": streaming}


@router.post(
    "/{chat_id}/messages/stream",
    dependencies=[Depends(_stream_rate_limit)],
)
async def stream_chat_message(
    chat_id: str,
    message: str = Form(...),
    files: list[UploadFile] = File(default_factory=list),
    auth: OrchidAuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
    chat_repo: OrchidChatStorage = Depends(get_chat_repo),
    runtime: OrchidRuntime = Depends(get_runtime),
    graph: Any = Depends(get_graph),
    mcp_token_store: OrchidMCPTokenStore | None = Depends(get_mcp_token_store_optional),
):
    """Send a message and stream the response as Server-Sent Events.

    Same file processing and auth as the non-streaming endpoint.
    Uses LangGraph's ``astream(stream_mode="messages")`` to yield
    tokens incrementally from the supervisor synthesis step.
    """
    request_id = uuid.uuid4().hex[:8]
    request_start = time.perf_counter()
    perf_logger.info(
        "[PERF][req=%s][stream] === REQUEST START === chat=%s files=%d msg_len=%d",
        request_id,
        chat_id[:8],
        len(files),
        len(message),
    )

    prep_start = time.perf_counter()
    prepared = await prepare_graph_state(
        chat_id,
        message,
        files,
        auth,
        settings,
        chat_repo=chat_repo,
        runtime=runtime,
        mcp_token_store=mcp_token_store,
    )
    prep_elapsed = (time.perf_counter() - prep_start) * 1000
    perf_logger.info("[PERF][req=%s][stream] prepare_graph_state took %.1f ms", request_id, prep_elapsed)

    metrics = OrchidMetricsHandler()
    generator = stream_supervisor_tokens(
        graph=graph,
        prepared=prepared,
        chat_id=chat_id,
        request_id=request_id,
        request_start=request_start,
        settings=settings,
        chat_repo=chat_repo,
        metrics=metrics,
    )

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
