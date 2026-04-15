"""Request / Response schemas and conversion helpers."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


# ── Request models ──────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    chat_id: str | None = None


class CreateChatRequest(BaseModel):
    title: str = ""


class SendMessageRequest(BaseModel):
    message: str


class IndexRequest(BaseModel):
    tenant_id: str = "default"


# ── Response models ─────────────────────────────────────────


class ChatResponse(BaseModel):
    response: str
    chat_id: str
    tenant_id: str
    agents_used: list[str]
    auth_required: list[str] = []  # MCP servers needing OAuth authorization


class ChatSessionOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    is_shared: bool


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    agents_used: list[str]
    created_at: str


class IndexResponse(BaseModel):
    status: str
    tenant_id: str
    indexed: dict[str, int]


# ── Conversion helpers ──────────────────────────────────────


def session_to_out(s) -> ChatSessionOut:
    return ChatSessionOut(
        id=s.id,
        title=s.title,
        created_at=s.created_at.isoformat() if isinstance(s.created_at, datetime) else str(s.created_at),
        updated_at=s.updated_at.isoformat() if isinstance(s.updated_at, datetime) else str(s.updated_at),
        is_shared=s.is_shared,
    )


def message_to_out(m) -> MessageOut:
    return MessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        agents_used=m.agents_used,
        created_at=m.created_at.isoformat() if isinstance(m.created_at, datetime) else str(m.created_at),
    )
