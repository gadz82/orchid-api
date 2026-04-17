"""SSE streaming endpoint for real-time agent responses.

Streams tokens from the supervisor synthesis step as Server-Sent Events.
The non-trivial "is this token a handoff or synthesis?" decision lives
in :mod:`._stream_buffer`; this module owns only the HTTP adapter.

SSE event format:
    data: {"type":"token","content":"Hello"}\n\n
    data: {"type":"status","agent":"menu","status":"started"}\n\n
    data: {"type":"done","response":"...","agents_used":[...],"auth_required":[...]}\n\n
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from orchid_ai.config.schema import AgentsConfig
from orchid_ai.core.mcp import MCPTokenStore
from orchid_ai.core.state import AuthContext
from orchid_ai.persistence.base import ChatStorage
from orchid_ai.runtime import OrchidRuntime

from ..auth import get_auth_context
from ..context import (
    get_agents_config_optional,
    get_chat_repo,
    get_graph,
    get_mcp_token_store_optional,
    get_runtime,
)
from ..settings import Settings, get_settings
from ._helpers import auto_title_if_first_message, prepare_graph_state
from ._stream_buffer import BufferedToken, SupervisorTokenBuffer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["streaming"])


@router.get("/capabilities")
async def get_capabilities(
    agents_config: AgentsConfig | None = Depends(get_agents_config_optional),
):
    """Return server capabilities so the frontend can detect streaming support.

    Reads ``agents_config`` cached at startup rather than re-parsing
    ``agents.yaml`` on every call.
    """
    streaming = agents_config.supervisor.streaming_enabled if agents_config is not None else False
    return {"streaming_enabled": streaming}


@router.post("/{chat_id}/messages/stream")
async def stream_chat_message(
    chat_id: str,
    message: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    auth: AuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
    chat_repo: ChatStorage = Depends(get_chat_repo),
    runtime: OrchidRuntime = Depends(get_runtime),
    graph: Any = Depends(get_graph),
    mcp_token_store: MCPTokenStore | None = Depends(get_mcp_token_store_optional),
):
    """
    Send a message and stream the response as Server-Sent Events.

    Same file processing and auth as the non-streaming endpoint.
    Uses LangGraph's ``astream(stream_mode="messages")`` to yield
    tokens incrementally from the supervisor synthesis step.
    """
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

    # Per-request streaming state
    seen_agents: set[str] = set()
    agent_results: dict[str, str] = {}  # agent_name → last substantial response
    agents_done: bool = False  # True after any agent has returned to supervisor
    full_response_parts: list[str] = []
    buffer = SupervisorTokenBuffer()

    def emit(event: BufferedToken) -> str:
        """Emit a BufferedToken as SSE; track it for final persistence."""
        if event.kind == "token":
            full_response_parts.append(event.content)
        return _sse({"type": event.kind, "content": event.content})

    async def event_generator():
        """Yield SSE events from the LangGraph stream.

        We ONLY stream the supervisor's **synthesis** step — the final
        response after all agents have completed.  Internal messages
        (routing decisions, agent prefixes, skill names) are suppressed.

        With ``stream_mode="messages"`` incremental LLM tokens arrive
        WITHOUT the ``[Supervisor →]`` prefix — that prefix is attached
        to the final assembled message.  We therefore buffer supervisor
        tokens and classify them retroactively when the next event
        reveals the context.
        """
        nonlocal agents_done

        try:
            graph_config = {"configurable": {"thread_id": chat_id}}
            async for msg, metadata in graph.astream(
                prepared.initial_state,
                config=graph_config,
                stream_mode="messages",
            ):
                node = metadata.get("langgraph_node", "")

                # ── Agent node messages ────────────────────────
                if node.endswith("_agent"):
                    # Buffered supervisor tokens were a sequential advance
                    # (handoff) — not synthesis.  Emit them as handoff.
                    for ev in buffer.discard_as_handoff():
                        yield emit(ev)

                    agent_name = node.removesuffix("_agent")
                    if agent_name not in seen_agents:
                        seen_agents.add(agent_name)
                        yield _sse({"type": "status", "agent": agent_name, "status": "started"})

                    content = getattr(msg, "content", "")
                    if content and isinstance(content, str):
                        tool_calls = getattr(msg, "tool_calls", None)
                        if not tool_calls:
                            clean = content
                            prefix = f"[{agent_name.title()} Agent]\n"
                            if clean.startswith(prefix):
                                clean = clean[len(prefix) :]
                            stripped = clean.strip()
                            if len(stripped) > 50 and " " in stripped and agent_name not in agent_results:
                                agent_results[agent_name] = stripped
                                preview = stripped[:200] + "…" if len(stripped) > 200 else stripped
                                yield _sse(
                                    {
                                        "type": "status",
                                        "agent": agent_name,
                                        "status": "done",
                                        "preview": preview,
                                    }
                                )

                    agents_done = True
                    continue

                # ── Only process supervisor node ───────────────
                if node != "supervisor":
                    continue

                content = getattr(msg, "content", "")
                if not content or not isinstance(content, str):
                    continue

                # Skip routing JSON (structured output)
                if content.strip().startswith("{"):
                    continue

                # Internal [Supervisor] messages (final assembled)
                if content.startswith("[Supervisor"):
                    if content.startswith("[Supervisor →"):
                        ev = buffer.record_inline_handoff(content)
                        if ev is not None:
                            yield emit(ev)
                    continue

                # Before agents have run, skip all supervisor noise
                if not agents_done:
                    continue

                # Skip tool call messages
                if getattr(msg, "tool_calls", None):
                    continue

                # Skip already-emitted content + echoes of buffered content
                if buffer.already_emitted(content) or buffer.would_duplicate(content):
                    continue

                buffer.append(content)

            # ── Stream ended: flush buffer as synthesis ─────────
            for ev in buffer.flush_as_tokens():
                yield emit(ev)

        except Exception as exc:
            logger.error("[Stream] Graph streaming error: %s", exc, exc_info=True)
            yield _sse({"type": "error", "message": "An error occurred while processing your request."})

        # ── Final event with complete metadata ──
        full_response = "".join(full_response_parts) or "No response generated."
        agents_used = sorted(seen_agents)
        auth_required = [name for name, ok in prepared.mcp_auth_status.items() if not ok]

        yield _sse(
            {
                "type": "done",
                "response": full_response,
                "agents_used": agents_used,
                "agent_results": agent_results,
                "auth_required": auth_required,
            }
        )

        # ── Persist after streaming completes ──
        try:
            await chat_repo.add_message(chat_id, "user", prepared.message)
            await chat_repo.add_message(chat_id, "assistant", full_response, agents_used=agents_used)
            await auto_title_if_first_message(chat_id, prepared.message, prepared.history_rows, chat_repo)
        except Exception as exc:
            logger.error("[Stream] Persistence error: %s", exc, exc_info=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


def _sse(data: dict) -> str:
    """Format a dict as an SSE event line."""
    return f"data: {json.dumps(data)}\n\n"
