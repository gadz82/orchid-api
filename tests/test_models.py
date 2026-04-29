"""Tests for orchid_api.models — request/response schemas and converters."""

from __future__ import annotations


from orchid_api.models import (
    ChatResponse,
    ChatSessionOut,
    CreateChatRequest,
    IndexRequest,
    MessageOut,
    SendMessageRequest,
    message_to_out,
    session_to_out,
)


# ── Request models ─────────────────────────────────────────


class TestCreateChatRequest:
    def test_default_title(self):
        r = CreateChatRequest()
        assert r.title == ""

    def test_custom_title(self):
        r = CreateChatRequest(title="My Chat")
        assert r.title == "My Chat"


class TestSendMessageRequest:
    def test_message_required(self):
        r = SendMessageRequest(message="hello")
        assert r.message == "hello"


class TestIndexRequest:
    def test_default_tenant(self):
        r = IndexRequest()
        assert r.tenant_id == "default"


# ── Response models ────────────────────────────────────────


class TestChatResponse:
    def test_fields(self):
        r = ChatResponse(response="ok", chat_id="c1", tenant_id="t1", agents_used=["a"])
        assert r.response == "ok"
        assert r.agents_used == ["a"]


class TestChatSessionOut:
    def test_fields(self):
        r = ChatSessionOut(id="c1", title="T", created_at="2024-01-01", updated_at="2024-01-01", is_shared=False)
        assert r.id == "c1"
        assert r.is_shared is False


class TestMessageOut:
    def test_fields(self):
        r = MessageOut(id="m1", role="user", content="hi", agents_used=[], created_at="2024-01-01")
        assert r.role == "user"


# ── Conversion helpers ─────────────────────────────────────


class TestSessionToOut:
    def test_datetime_conversion(self, sample_session):
        out = session_to_out(sample_session)
        assert out.id == "chat-001"
        assert out.title == "Test Chat"
        assert out.is_shared is False
        assert "T" in out.created_at  # ISO format has T separator

    def test_string_date_passthrough(self):
        """When dates are already strings, they pass through as str()."""
        session = type(
            "S",
            (),
            {
                "id": "x",
                "title": "t",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
                "is_shared": False,
            },
        )()
        out = session_to_out(session)
        assert out.created_at == "2024-01-01"


class TestMessageToOut:
    def test_datetime_conversion(self, sample_message):
        out = message_to_out(sample_message)
        assert out.id == "msg-001"
        assert out.role == "user"
        assert out.content == "Hello"
        assert "T" in out.created_at

    def test_string_date_passthrough(self):
        msg = type(
            "M",
            (),
            {
                "id": "m",
                "role": "assistant",
                "content": "hi",
                "agents_used": ["a1"],
                "created_at": "2024-06-15",
            },
        )()
        out = message_to_out(msg)
        assert out.agents_used == ["a1"]
        assert out.created_at == "2024-06-15"
