"""SSE streaming endpoint for real-time agent responses.

Streams tokens from the supervisor synthesis step as Server-Sent Events.
Falls back gracefully if streaming is disabled in config.

SSE event format:
    data: {"type":"token","content":"Hello"}\n\n
    data: {"type":"status","agent":"menu","status":"started"}\n\n
    data: {"type":"done","response":"...","agents_used":[...],"auth_required":[...]}\n\n
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from orchid_ai.core.state import AuthContext

from ..auth import get_auth_context
from ..context import app_ctx
from ..settings import Settings, get_settings
from ._helpers import prepare_graph_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["streaming"])


@router.get("/capabilities")
async def get_capabilities():
    """Return server capabilities so the frontend can detect streaming support."""
    streaming = False
    if app_ctx.runtime and app_ctx.runtime.mcp_auth_registry is not None:
        # Graph is loaded — check supervisor config
        try:
            from orchid_ai.config.loader import load_config
            import os

            config_path = os.environ.get("AGENTS_CONFIG_PATH", "agents.yaml")
            config = load_config(config_path)
            streaming = config.supervisor.streaming_enabled
        except Exception:
            streaming = True  # default to enabled if config read fails

    return {"streaming_enabled": streaming}


@router.post("/{chat_id}/messages/stream")
async def stream_chat_message(
    chat_id: str,
    message: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    auth: AuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
):
    """
    Send a message and stream the response as Server-Sent Events.

    Same file processing and auth as the non-streaming endpoint.
    Uses LangGraph's ``astream(stream_mode="messages")`` to yield
    tokens incrementally from the supervisor synthesis step.
    """
    prepared = await prepare_graph_state(chat_id, message, files, auth, settings)

    # Track active agents and full response for persistence
    seen_agents: set[str] = set()
    _seen_agent_results: set[str] = set()  # dedup agent results
    _seen_handoffs: set[str] = set()  # dedup handoff messages
    agents_done: bool = False  # True after at least one agent has returned to supervisor
    full_response_parts: list[str] = []

    async def event_generator():
        """Yield SSE events from the LangGraph stream.

        We ONLY stream the supervisor's **synthesis** step — the final
        response after all agents have completed.  Internal messages
        (routing decisions, agent prefixes, skill names) are suppressed.

        Detection: synthesis happens when the supervisor node emits
        content AFTER at least one agent has been seen.
        """
        nonlocal agents_done

        try:
            async for msg, metadata in app_ctx.graph.astream(
                prepared.initial_state,
                stream_mode="messages",
            ):
                node = metadata.get("langgraph_node", "")

                # Track which agents are active and capture their final response
                if node.endswith("_agent"):
                    agent_name = node.removesuffix("_agent")
                    if agent_name not in seen_agents:
                        seen_agents.add(agent_name)
                        yield _sse({"type": "status", "agent": agent_name, "status": "started"})

                    # Capture the agent's final text response (the [AgentName Agent] message)
                    content = getattr(msg, "content", "")
                    if content and isinstance(content, str):
                        tool_calls = getattr(msg, "tool_calls", None)
                        if not tool_calls:
                            # Strip the "[AgentName Agent]\n" prefix if present
                            clean = content
                            prefix = f"[{agent_name.title()} Agent]\n"
                            if clean.startswith(prefix):
                                clean = clean[len(prefix) :]
                            # Only emit if it's substantial (skip skill names, short noise)
                            stripped = clean.strip()
                            if len(stripped) > 50 and " " in stripped:
                                # Deduplicate: the same content may come twice (streaming + final)
                                key = f"{agent_name}:{clean[:100]}"
                                if key not in _seen_agent_results:
                                    _seen_agent_results.add(key)
                                    yield _sse(
                                        {
                                            "type": "agent_result",
                                            "agent": agent_name,
                                            "content": clean.strip(),
                                        }
                                    )

                    agents_done = True
                    continue  # Don't stream agent messages as main tokens

                # Only stream from the supervisor AFTER agents have run (= synthesis)
                # OR when the supervisor gives a direct response (no agents activated)
                if node != "supervisor":
                    continue
                # Skip the routing step (first supervisor invocation, before any agent runs)
                # Direct responses are detected by the supervisor setting final_response
                # without activating agents — we let those through
                if not agents_done:
                    # Check if this looks like a direct response (not a routing JSON)
                    c = getattr(msg, "content", "")
                    if c and c.strip().startswith("{"):
                        continue  # routing JSON — skip

                content = getattr(msg, "content", "")
                if not content or not isinstance(content, str):
                    continue

                # Skip internal supervisor messages
                if content.startswith("[Supervisor"):
                    # Handoff messages (sequential advance) → emit as status, not token
                    if content.startswith("[Supervisor →"):
                        handoff_text = content.split("] ", 1)[-1] if "] " in content else content
                        handoff_text = _clean_handoff(handoff_text)
                        if handoff_text and handoff_text not in _seen_handoffs:
                            _seen_handoffs.add(handoff_text)
                            yield _sse({"type": "handoff", "content": handoff_text})
                    continue

                # Skip handoff-style messages that don't have the [Supervisor prefix
                # (sometimes the synthesis LLM echoes the handoff preamble)
                _lower = content.lower()
                if "handoff message" in _lower and len(content) < 500:
                    cleaned = _clean_handoff(content)
                    if cleaned and cleaned not in _seen_handoffs:
                        _seen_handoffs.add(cleaned)
                        yield _sse({"type": "handoff", "content": cleaned})
                    continue

                # Only stream text content (not tool calls)
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    continue

                # Deduplicate: astream emits both incremental chunks and the
                # final complete message.  Skip if we already sent this exact content.
                if full_response_parts and content == full_response_parts[-1]:
                    continue  # duplicate of the last emitted chunk
                full_response_parts.append(content)
                yield _sse({"type": "token", "content": content})

        except Exception as exc:
            logger.error("[Stream] Graph streaming error: %s", exc, exc_info=True)
            yield _sse({"type": "error", "message": str(exc)[:200]})

        # ── Final event with complete metadata ──
        full_response = "".join(full_response_parts) or "No response generated."
        agents_used = sorted(seen_agents)
        auth_required = [name for name, ok in prepared.mcp_auth_status.items() if not ok]

        yield _sse(
            {
                "type": "done",
                "response": full_response,
                "agents_used": agents_used,
                "auth_required": auth_required,
            }
        )

        # ── Persist after streaming completes ──
        try:
            await app_ctx.chat_repo.add_message(chat_id, "user", prepared.message)
            await app_ctx.chat_repo.add_message(chat_id, "assistant", full_response, agents_used=agents_used)

            if not prepared.history_rows:
                title = prepared.message[:50].strip()
                if len(prepared.message) > 50:
                    title += "…"
                await app_ctx.chat_repo.update_title(chat_id, title)
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


# ── Preamble patterns the LLM wraps handoff content in ──
_HANDOFF_PREAMBLES = [
    "here is the handoff message:",
    "here is a brief handoff message:",
    "here is a brief handoff message that summarises",
    "handoff message:",
]


def _clean_handoff(text: str) -> str:
    """Strip LLM preamble from handoff messages and clean up."""
    cleaned = text.strip()
    # Strip known preambles (case-insensitive)
    lower = cleaned.lower()
    for preamble in _HANDOFF_PREAMBLES:
        if lower.startswith(preamble):
            cleaned = cleaned[len(preamble) :].strip()
            break
    # Strip surrounding quotes
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1].strip()
    return cleaned
