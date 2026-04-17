"""Tests for ``orchid_api.routers._stream_buffer``.

The buffer encapsulates the "decide retroactively" logic that used to
live inline in ``streaming.py`` — these tests lock in the classification
behaviour so future refactors can't silently break handoff/synthesis
discrimination.
"""

from __future__ import annotations

from orchid_api.routers._stream_buffer import (
    SupervisorTokenBuffer,
    clean_handoff,
)


class TestCleanHandoff:
    def test_strips_known_preamble(self):
        assert clean_handoff("Here is the handoff message: go ahead") == "go ahead"

    def test_strips_surrounding_quotes(self):
        assert clean_handoff('"ready"') == "ready"

    def test_unchanged_when_plain(self):
        assert clean_handoff("just text") == "just text"

    def test_case_insensitive_preamble(self):
        assert clean_handoff("HANDOFF MESSAGE: go") == "go"


class TestBufferState:
    def test_fresh_buffer_has_no_content(self):
        buf = SupervisorTokenBuffer()
        assert buf.has_content is False

    def test_append_then_has_content(self):
        buf = SupervisorTokenBuffer()
        buf.append("hello")
        assert buf.has_content is True

    def test_clear_empties_buffer(self):
        buf = SupervisorTokenBuffer()
        buf.append("hello")
        buf.clear()
        assert buf.has_content is False


class TestDuplicateDetection:
    def test_would_duplicate_exact_concat(self):
        buf = SupervisorTokenBuffer()
        buf.append("hel")
        buf.append("lo")
        assert buf.would_duplicate("hello") is True

    def test_would_duplicate_prefix_match(self):
        buf = SupervisorTokenBuffer()
        buf.append("hello world extended")
        assert buf.would_duplicate("hello") is True

    def test_would_duplicate_last_chunk_echo(self):
        buf = SupervisorTokenBuffer()
        buf.append("first")
        buf.append("second")
        assert buf.would_duplicate("second") is True

    def test_not_duplicate_when_new(self):
        buf = SupervisorTokenBuffer()
        buf.append("hello")
        assert buf.would_duplicate("world") is False

    def test_not_duplicate_when_empty(self):
        buf = SupervisorTokenBuffer()
        assert buf.would_duplicate("anything") is False

    def test_already_emitted_tracks_prefix(self):
        buf = SupervisorTokenBuffer()
        buf.mark_emitted("some-handoff-text")
        assert buf.already_emitted("some-handoff-text") is True
        assert buf.already_emitted("different content") is False


class TestFlushAsTokens:
    def test_flush_emits_tokens_in_order(self):
        buf = SupervisorTokenBuffer()
        buf.append("a")
        buf.append("b")
        buf.append("c")

        events = list(buf.flush_as_tokens())

        assert [e.content for e in events] == ["a", "b", "c"]
        assert all(e.kind == "token" for e in events)
        assert buf.has_content is False

    def test_flush_skips_already_emitted(self):
        buf = SupervisorTokenBuffer()
        buf.mark_emitted("dup")
        buf.append("dup")
        buf.append("fresh")

        events = list(buf.flush_as_tokens())

        assert [e.content for e in events] == ["fresh"]

    def test_flush_empty_is_noop(self):
        buf = SupervisorTokenBuffer()
        assert list(buf.flush_as_tokens()) == []


class TestDiscardAsHandoff:
    def test_concatenates_then_emits_handoff(self):
        buf = SupervisorTokenBuffer()
        buf.append("handing ")
        buf.append("off now")

        events = list(buf.discard_as_handoff())

        assert len(events) == 1
        assert events[0].kind == "handoff"
        assert events[0].content == "handing off now"
        assert buf.has_content is False

    def test_strips_preamble(self):
        buf = SupervisorTokenBuffer()
        buf.append("Here is the handoff message: ")
        buf.append("proceed")

        events = list(buf.discard_as_handoff())

        assert events[0].content == "proceed"

    def test_dedup_suppresses_repeat(self):
        buf = SupervisorTokenBuffer()
        buf.append("same handoff")
        first = list(buf.discard_as_handoff())

        buf.append("same handoff")
        second = list(buf.discard_as_handoff())

        assert len(first) == 1
        assert second == []

    def test_empty_buffer_emits_nothing(self):
        buf = SupervisorTokenBuffer()
        assert list(buf.discard_as_handoff()) == []

    def test_whitespace_only_emits_nothing(self):
        buf = SupervisorTokenBuffer()
        buf.append("   ")
        buf.append("\n")
        assert list(buf.discard_as_handoff()) == []


class TestRecordInlineHandoff:
    def test_extracts_handoff_text(self):
        buf = SupervisorTokenBuffer()
        ev = buf.record_inline_handoff("[Supervisor → bookings] please proceed")

        assert ev is not None
        assert ev.kind == "handoff"
        assert ev.content == "please proceed"

    def test_clears_existing_buffer(self):
        buf = SupervisorTokenBuffer()
        buf.append("advance-llm-call")
        buf.record_inline_handoff("[Supervisor → menu] go")
        assert buf.has_content is False

    def test_dedup_returns_none(self):
        buf = SupervisorTokenBuffer()
        first = buf.record_inline_handoff("[Supervisor → a] same content")
        second = buf.record_inline_handoff("[Supervisor → a] same content")
        assert first is not None
        assert second is None
