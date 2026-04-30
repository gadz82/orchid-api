"""SSE token-stream generator extracted from :mod:`streaming`.

The route handler in :mod:`streaming` orchestrates request lifecycle
(auth, prepare state, build response). The actual streaming logic — the
graph driver, token buffer interaction, agent-status events, persistence
— lives here so it can be unit-tested without spinning up a FastAPI
``StreamingResponse``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator

from orchid_ai.observability import OrchidMetricsHandler, extract_event
from orchid_ai.persistence.base import OrchidChatStorage

from ..settings import Settings
from ._helpers import PreparedState, auto_title_if_first_message
from ._stream_buffer import BufferedToken, SupervisorTokenBuffer

logger = logging.getLogger(__name__)
perf_logger = logging.getLogger("orchid.perf")


def sse_event(payload: dict) -> str:
    """Format a dict as an SSE ``data:`` frame."""
    return f"data: {json.dumps(payload)}\n\n"


async def stream_supervisor_tokens(
    *,
    graph: Any,
    prepared: PreparedState,
    chat_id: str,
    request_id: str,
    request_start: float,
    settings: Settings,
    chat_repo: OrchidChatStorage,
    metrics: OrchidMetricsHandler,
) -> AsyncIterator[str]:
    """Drive a graph run and yield SSE frames.

    Owns four cross-cutting concerns the original 161-line closure
    bundled together:

      1. Token buffering / agent-status emission via
         :class:`SupervisorTokenBuffer`.
      2. Hard timeout via :func:`asyncio.timeout`.
      3. Cancellation cleanup so a disconnected client doesn't trigger
         message persistence.
      4. Best-effort persistence of the user + assistant messages and
         auto-titling once the stream terminates normally.

    Yields SSE ``data:`` frames as plain strings.
    """
    seen_agents: set[str] = set()
    agent_results: dict[str, str] = {}
    agents_done = False
    full_response_parts: list[str] = []
    buffer = SupervisorTokenBuffer()
    first_token_at: list[float | None] = [None]

    def emit(event: BufferedToken) -> str:
        if event.kind == "token":
            if first_token_at[0] is None:
                first_token_at[0] = time.perf_counter()
                ttft = (first_token_at[0] - request_start) * 1000
                perf_logger.info("[PERF][req=%s][stream] TTFT (time-to-first-token) = %.1f ms", request_id, ttft)
            full_response_parts.append(event.content)
        return sse_event({"type": event.kind, "content": event.content})

    graph_config = {
        "configurable": {"thread_id": chat_id, "request_id": request_id},
        "callbacks": [metrics],
    }
    direct_final: str | None = None
    graph_start = time.perf_counter()
    timed_out = False

    try:
        async with asyncio.timeout(settings.stream_max_seconds):
            async for mode, payload in graph.astream(
                prepared.initial_state,
                config=graph_config,
                stream_mode=["messages", "values"],
            ):
                if mode == "values":
                    fr = payload.get("final_response") if isinstance(payload, dict) else None
                    if fr:
                        direct_final = fr
                    continue

                msg, metadata = payload
                node = metadata.get("langgraph_node", "")

                # ── Phase B: mini-agent lifecycle events ────────
                # Translate ``mini_agent.*`` SystemMessages into SSE
                # frames before any other processing — they are
                # invisible to the user-visible synthesis stream and
                # must not be counted as agent-status / token events.
                event = extract_event(msg)
                if event is not None:
                    event_name, event_data = event
                    yield sse_event({"type": event_name, **event_data})
                    continue

                # ── Phase B: suppress per-mini token streams by default ──
                # ``mini_agent.stream_inner_tokens=true`` (opt-in) is
                # not yet wired here — the default ``false`` semantics
                # (spec §13) is implemented as: drop every chunk
                # produced by a ``*_mini`` node so the user-visible
                # synthesis stream stays clean.
                if node.endswith("_mini") or node.endswith("_aggregator"):
                    continue

                if node.endswith("_agent"):
                    for ev in buffer.discard_as_handoff():
                        yield emit(ev)

                    agent_name = node.removesuffix("_agent")
                    if agent_name not in seen_agents:
                        seen_agents.add(agent_name)
                        yield sse_event({"type": "status", "agent": agent_name, "status": "started"})

                    agent_status = _maybe_emit_agent_done(msg, agent_name, agent_results)
                    if agent_status is not None:
                        yield agent_status

                    agents_done = True
                    continue

                if node != "supervisor":
                    continue

                content = getattr(msg, "content", "")
                if not content or not isinstance(content, str):
                    continue

                if content.strip().startswith("{"):
                    continue

                if content.startswith("[Supervisor"):
                    if content.startswith("[Supervisor →"):
                        ev = buffer.record_inline_handoff(content)
                        if ev is not None:
                            yield emit(ev)
                    continue

                if not agents_done:
                    continue

                if getattr(msg, "tool_calls", None):
                    continue

                if buffer.already_emitted(content) or buffer.would_duplicate(content):
                    continue

                buffer.append(content)

            for ev in buffer.flush_as_tokens():
                yield emit(ev)

            # Direct-response / skipped-synthesis fallback: ``final_response``
            # may have been set in the values stream without producing any
            # streamed tokens. Emit it as a single token so the UI renders it.
            if not full_response_parts and direct_final:
                yield emit(BufferedToken(kind="token", content=direct_final))

    except asyncio.CancelledError:
        logger.info(
            "[Stream] Client disconnected mid-stream req=%s chat=%s",
            request_id,
            chat_id[:8],
        )
        raise
    except TimeoutError:
        timed_out = True
        logger.warning(
            "[Stream] Stream exceeded %ds budget req=%s chat=%s",
            settings.stream_max_seconds,
            request_id,
            chat_id[:8],
        )
        yield sse_event(
            {
                "type": "error",
                "message": (
                    f"Stream exceeded the {settings.stream_max_seconds}s budget — showing what we have so far."
                ),
            }
        )
    except Exception as exc:
        logger.error("[Stream] Graph streaming error: %s", exc, exc_info=True)
        yield sse_event({"type": "error", "message": "An error occurred while processing your request."})

    graph_elapsed = (time.perf_counter() - graph_start) * 1000

    full_response = "".join(full_response_parts) or "No response generated."
    agents_used = sorted(seen_agents)
    auth_required = [name for name, ok in prepared.mcp_auth_status.items() if not ok]

    yield sse_event(
        {
            "type": "done",
            "response": full_response,
            "agents_used": agents_used,
            "agent_results": agent_results,
            "auth_required": auth_required,
            "timed_out": timed_out,
        }
    )

    persist_start = time.perf_counter()
    try:
        await chat_repo.add_message(chat_id, "user", prepared.message)
        await chat_repo.add_message(chat_id, "assistant", full_response, agents_used=agents_used)
        await auto_title_if_first_message(chat_id, prepared.message, prepared.history_rows, chat_repo)
    except Exception as exc:
        logger.error("[Stream] Persistence error: %s", exc, exc_info=True)
    persist_elapsed = (time.perf_counter() - persist_start) * 1000

    total_elapsed = (time.perf_counter() - request_start) * 1000
    m = metrics.get_metrics()
    perf_logger.info(
        "[PERF][req=%s][stream] graph.astream took %.1f ms | persist=%.1f ms | total=%.1f ms",
        request_id,
        graph_elapsed,
        persist_elapsed,
        total_elapsed,
    )
    perf_logger.info(
        "[PERF][req=%s][stream] LLM stats: calls=%d errors=%d avg_latency=%.3fs total_tokens=%d (prompt=%d completion=%d)",
        request_id,
        m["llm_calls"],
        m["llm_errors"],
        m["avg_llm_latency_s"],
        m["total_tokens"],
        m["prompt_tokens"],
        m["completion_tokens"],
    )
    perf_logger.info(
        "[PERF][req=%s][stream] Tool stats: tool_calls=%d retries=%d",
        request_id,
        m["tool_calls"],
        m["retries"],
    )
    if m["agent_latencies_s"]:
        perf_logger.info(
            "[PERF][req=%s][stream] Agent latencies (avg s): %s | call_counts: %s",
            request_id,
            m["agent_latencies_s"],
            m["agent_call_counts"],
        )
    perf_logger.info(
        "[PERF][req=%s][stream] === REQUEST END === total=%.1f ms agents=%s",
        request_id,
        total_elapsed,
        agents_used,
    )


def _maybe_emit_agent_done(
    msg: Any,
    agent_name: str,
    agent_results: dict[str, str],
) -> str | None:
    """Return a ``status: done`` SSE frame for the first substantial reply from an agent.

    Returns ``None`` when the message has tool calls, no content, or
    the agent already had its ``done`` event emitted in this turn.
    """
    if agent_name in agent_results:
        return None
    content = getattr(msg, "content", "")
    if not content or not isinstance(content, str):
        return None
    if getattr(msg, "tool_calls", None):
        return None
    clean = content
    prefix = f"[{agent_name.title()} Agent]\n"
    if clean.startswith(prefix):
        clean = clean[len(prefix) :]
    stripped = clean.strip()
    if len(stripped) <= 50 or " " not in stripped:
        return None
    agent_results[agent_name] = stripped
    preview = stripped[:200] + "…" if len(stripped) > 200 else stripped
    return sse_event(
        {
            "type": "status",
            "agent": agent_name,
            "status": "done",
            "preview": preview,
        }
    )
