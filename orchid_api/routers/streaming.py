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
    agent_results: dict[str, str] = {}  # agent_name → last substantial response
    _seen_handoffs: set[str] = set()  # dedup handoff messages
    _emitted_content: set[str] = set()  # dedup: content already emitted as handoff/status
    agents_done: bool = False  # True after at least one agent has returned to supervisor
    full_response_parts: list[str] = []

    # Buffer for supervisor tokens between agent completions.
    # We cannot tell if supervisor tokens are a sequential handoff or
    # the final synthesis until the NEXT event arrives:
    #   - Next event is an agent node → buffer was handoff → discard
    #   - Next event is [Supervisor →] → buffer was handoff → discard
    #   - Stream ends → buffer was synthesis → flush as tokens
    _supervisor_buffer: list[str] = []

    async def _flush_buffer_as_tokens():
        """Emit buffered supervisor content as synthesis tokens."""
        nonlocal full_response_parts
        for chunk in _supervisor_buffer:
            if chunk not in _emitted_content:
                full_response_parts.append(chunk)
                yield _sse({"type": "token", "content": chunk})
        _supervisor_buffer.clear()

    async def _discard_buffer_as_handoff():
        """Emit buffered supervisor content as a handoff, then clear."""
        combined = "".join(_supervisor_buffer).strip()
        if combined:
            cleaned = _clean_handoff(combined)
            if cleaned and cleaned not in _seen_handoffs:
                _seen_handoffs.add(cleaned)
                _emitted_content.add(cleaned[:100])
                yield _sse({"type": "handoff", "content": cleaned})
        _supervisor_buffer.clear()

    async def event_generator():
        """Yield SSE events from the LangGraph stream.

        We ONLY stream the supervisor's **synthesis** step — the final
        response after all agents have completed.  Internal messages
        (routing decisions, agent prefixes, skill names) are suppressed.

        Key insight: with ``stream_mode="messages"``, incremental LLM
        tokens from the supervisor arrive WITHOUT the ``[Supervisor →]``
        prefix — that prefix is added to the final assembled message.
        We cannot tell if tokens are handoff or synthesis until the next
        event reveals the context.  Solution: buffer supervisor tokens
        and decide retroactively.
        """
        nonlocal agents_done

        try:
            graph_config = {"configurable": {"thread_id": chat_id}}
            async for msg, metadata in app_ctx.graph.astream(
                prepared.initial_state,
                config=graph_config,
                stream_mode="messages",
            ):
                node = metadata.get("langgraph_node", "")

                # ── Agent node messages ────────────────────────
                if node.endswith("_agent"):
                    # If we had buffered supervisor tokens, they were a
                    # sequential advance (handoff) — not synthesis.
                    if _supervisor_buffer:
                        async for event in _discard_buffer_as_handoff():
                            yield event

                    agent_name = node.removesuffix("_agent")
                    if agent_name not in seen_agents:
                        seen_agents.add(agent_name)
                        yield _sse({"type": "status", "agent": agent_name, "status": "started"})

                    # Capture the agent's final text response
                    content = getattr(msg, "content", "")
                    if content and isinstance(content, str):
                        tool_calls = getattr(msg, "tool_calls", None)
                        if not tool_calls:
                            clean = content
                            prefix = f"[{agent_name.title()} Agent]\n"
                            if clean.startswith(prefix):
                                clean = clean[len(prefix) :]
                            stripped = clean.strip()
                            if len(stripped) > 50 and " " in stripped:
                                if agent_name not in agent_results:
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

                # Skip internal [Supervisor] messages (final assembled)
                if content.startswith("[Supervisor"):
                    if content.startswith("[Supervisor →"):
                        # Discard any buffered tokens (they were the advance LLM call)
                        _supervisor_buffer.clear()
                        # Emit the handoff text
                        handoff_text = content.split("] ", 1)[-1] if "] " in content else content
                        handoff_text = _clean_handoff(handoff_text)
                        if handoff_text and handoff_text not in _seen_handoffs:
                            _seen_handoffs.add(handoff_text)
                            _emitted_content.add(handoff_text[:100])
                            yield _sse({"type": "handoff", "content": handoff_text})
                    continue

                # Before agents have run, skip all supervisor noise
                if not agents_done:
                    continue

                # Skip tool call messages
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    continue

                # Skip already-emitted content
                if content[:100] in _emitted_content:
                    continue

                # Deduplicate: astream emits incremental chunks AND the
                # final assembled message.  The final message contains ALL
                # the chunk text, so skip it if the buffer already has content
                # that this message would duplicate.
                if _supervisor_buffer:
                    # If this content is longer than any single buffered chunk,
                    # it's likely the final assembled message — skip it.
                    buffered_combined = "".join(_supervisor_buffer)
                    if content == buffered_combined or buffered_combined.startswith(content):
                        continue
                    # Also skip if it's identical to the last buffered chunk
                    if content == _supervisor_buffer[-1]:
                        continue

                # Buffer supervisor tokens — we'll decide whether they're
                # handoff or synthesis when the next event arrives.
                _supervisor_buffer.append(content)

            # ── Stream ended: flush buffer as synthesis ─────────
            if _supervisor_buffer:
                async for event in _flush_buffer_as_tokens():
                    yield event

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
                "agent_results": agent_results,  # {agent_name: response_text}
                "auth_required": auth_required,
            }
        )

        # ── Persist after streaming completes ──
        try:
            from ._helpers import auto_title_if_first_message

            await app_ctx.chat_repo.add_message(chat_id, "user", prepared.message)
            await app_ctx.chat_repo.add_message(chat_id, "assistant", full_response, agents_used=agents_used)
            await auto_title_if_first_message(chat_id, prepared.message, prepared.history_rows)
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
