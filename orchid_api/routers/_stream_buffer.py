"""Supervisor token buffering for the SSE streaming endpoint.

LangGraph's ``astream(stream_mode="messages")`` emits supervisor tokens
incrementally BEFORE the ``[Supervisor → agent]`` final assembled message
arrives — so at the moment of emission we cannot tell whether the
buffered tokens are a sequential-handoff side-effect or the final
synthesis the user actually wants to see.

:class:`SupervisorTokenBuffer` encapsulates that "decide retroactively"
logic so it can be unit-tested in isolation.  The streaming router
owns one instance per request and consults it at each event.

Decision table (``next_event`` → action):

+---------------------+---------------------------------------------+
| Next LangGraph event | Meaning of buffered supervisor tokens      |
+=====================+=============================================+
| Agent node          | Handoff prep — ``discard_as_handoff()``    |
+---------------------+---------------------------------------------+
| ``[Supervisor →]``  | Handoff prep — ``discard_as_handoff()``    |
+---------------------+---------------------------------------------+
| Stream ends         | Final synthesis — ``flush_as_tokens()``    |
+---------------------+---------------------------------------------+
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# ── Preamble patterns the LLM wraps handoff content in ──────
_HANDOFF_PREAMBLES = [
    "here is the handoff message:",
    "here is a brief handoff message:",
    "here is a brief handoff message that summarises",
    "handoff message:",
]


def clean_handoff(text: str) -> str:
    """Strip LLM preamble from handoff messages and surrounding quotes."""
    cleaned = text.strip()
    lower = cleaned.lower()
    for preamble in _HANDOFF_PREAMBLES:
        if lower.startswith(preamble):
            cleaned = cleaned[len(preamble) :].strip()
            break
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1].strip()
    return cleaned


@dataclass
class BufferedToken:
    """A single supervisor chunk with its disposition."""

    kind: str  # "token" or "handoff"
    content: str


@dataclass
class SupervisorTokenBuffer:
    """Buffer supervisor chunks; classify them on the next event.

    Usage — see ``routers/streaming.py`` for the live integration::

        buf = SupervisorTokenBuffer()
        async for msg, meta in graph.astream(...):
            if meta["langgraph_node"].endswith("_agent"):
                for item in buf.discard_as_handoff():
                    yield sse(item)
                ...
            elif meta["langgraph_node"] == "supervisor":
                if msg.content.startswith("[Supervisor →"):
                    buf.clear()
                    ...
                else:
                    if buf.would_duplicate(msg.content):
                        continue
                    buf.append(msg.content)

        for item in buf.flush_as_tokens():
            yield sse(item)
    """

    _chunks: list[str] = field(default_factory=list)
    # Deduplication — content we've already emitted as either a token
    # or a handoff.  The streaming router uses a 100-char prefix as the
    # dedup key, matching the prior behaviour.
    _emitted_prefixes: set[str] = field(default_factory=set)
    _seen_handoffs: set[str] = field(default_factory=set)

    # ── Population ─────────────────────────────────────────

    def append(self, chunk: str) -> None:
        """Buffer a supervisor chunk for later classification."""
        self._chunks.append(chunk)

    def clear(self) -> None:
        """Drop any buffered content without emitting."""
        self._chunks.clear()

    def mark_emitted(self, content: str) -> None:
        """Remember that ``content`` has been emitted (as handoff or token)."""
        self._emitted_prefixes.add(content[:100])

    @property
    def has_content(self) -> bool:
        return bool(self._chunks)

    # ── Duplicate detection ───────────────────────────────

    def already_emitted(self, content: str) -> bool:
        """Return True if the first 100 chars match something we emitted."""
        return content[:100] in self._emitted_prefixes

    def would_duplicate(self, content: str) -> bool:
        """Return True if ``content`` is an echo of what's already buffered.

        ``astream`` emits incremental chunks PLUS a final assembled
        message that contains all the chunk text concatenated — so we
        skip it if the buffer's contents already cover it.
        """
        if not self._chunks:
            return False
        combined = "".join(self._chunks)
        if content == combined or combined.startswith(content):
            return True
        if content == self._chunks[-1]:
            return True
        return False

    # ── Emission (generators of ``BufferedToken``) ────────

    def flush_as_tokens(self) -> Iterable[BufferedToken]:
        """Emit buffered chunks as synthesis tokens; clear the buffer."""
        for chunk in self._chunks:
            if chunk[:100] in self._emitted_prefixes:
                continue
            yield BufferedToken(kind="token", content=chunk)
        self._chunks.clear()

    def discard_as_handoff(self) -> Iterable[BufferedToken]:
        """Emit buffered chunks as a single handoff message; clear the buffer.

        Duplicate handoffs (same cleaned text) are suppressed.
        """
        combined = "".join(self._chunks).strip()
        self._chunks.clear()
        if not combined:
            return
        cleaned = clean_handoff(combined)
        if not cleaned or cleaned in self._seen_handoffs:
            return
        self._seen_handoffs.add(cleaned)
        self._emitted_prefixes.add(cleaned[:100])
        yield BufferedToken(kind="handoff", content=cleaned)

    def record_inline_handoff(self, content: str) -> BufferedToken | None:
        """Record a ``[Supervisor → agent]`` message emitted inline.

        Clears any buffered tokens (they were the advance LLM call that
        produced this handoff), cleans the handoff text, and returns the
        handoff event — or ``None`` if we've already emitted this exact
        handoff.
        """
        self._chunks.clear()
        handoff_text = content.split("] ", 1)[-1] if "] " in content else content
        cleaned = clean_handoff(handoff_text)
        if not cleaned or cleaned in self._seen_handoffs:
            return None
        self._seen_handoffs.add(cleaned)
        self._emitted_prefixes.add(cleaned[:100])
        return BufferedToken(kind="handoff", content=cleaned)
