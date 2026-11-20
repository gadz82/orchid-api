"""Request / Response schemas and conversion helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Request models ──────────────────────────────────────────


class CreateChatRequest(BaseModel):
    title: str = ""


class SendMessageRequest(BaseModel):
    message: str


class IndexRequest(BaseModel):
    tenant_id: str = "default"


class PipelineRunRequest(BaseModel):
    """One sync or async pipeline run request.

    ``mode`` selects the inline (200) or Bloom (202) path. ``seed`` is the
    optional list of graph-object mappings the run starts from; ``include_graph``
    is opt-in because the snapshot can be large.
    """

    input: dict[str, Any]
    seed: list[dict[str, Any]] = Field(default_factory=list)
    mode: Literal["sync", "async"] = "sync"
    include_graph: bool = False
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600, allow_inf_nan=False)
    idempotency_key: str | None = Field(default=None, max_length=200)

    model_config = ConfigDict(extra="forbid")


class PipelineValidateRequest(BaseModel):
    """Validate one pipeline input without running anything."""

    input: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


# ── Pipeline response models ───────────────────────────────


class PipelineJobOut(BaseModel):
    id: str
    kind: str
    depends_on: list[str] = Field(default_factory=list)


class PipelineExposeOut(BaseModel):
    as_tool: bool
    requires_approval: bool = False
    parallel_safe: bool = False
    require_role: str | None = None
    max_tool_result_chars: int | None = None
    interpretation: str | None = None


class PipelineAsyncOut(BaseModel):
    enabled: bool
    mode: str | None = None


class PipelineInfoOut(BaseModel):
    name: str
    description: str
    version: str
    jobs: list[PipelineJobOut] = Field(default_factory=list)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    expose: PipelineExposeOut
    async_: PipelineAsyncOut = Field(alias="async", serialization_alias="async")

    model_config = ConfigDict(populate_by_name=True)


class PipelineListResponse(BaseModel):
    items: list[PipelineInfoOut]
    total: int
    limit: int
    offset: int


class PipelineValidateResponse(BaseModel):
    valid: bool
    errors: list[dict[str, Any]] = Field(default_factory=list)


class PipelineAsyncResponse(BaseModel):
    run_id: str | None = None
    signal_id: str
    pipeline: str
    status: str
    deduplicated: bool = False
    status_url: str | None = None


class PipelineRunResponse(BaseModel):
    run_id: str
    pipeline: str
    status: str
    output: dict[str, Any] | None = None
    summary: str = ""
    summary_source: str | None = None
    jobs: dict[str, Any] = Field(default_factory=dict)
    graph_stats: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    graph: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


# ── Response models ─────────────────────────────────────────


class ChatResponse(BaseModel):
    response: str
    chat_id: str
    tenant_id: str
    agents_used: list[str]
    auth_required: list[str] = []  # MCP servers needing OAuth authorization


class ToolApprovalRequest(BaseModel):
    """A single tool call that needs human approval before execution."""

    tool: str
    args: dict[str, Any] = {}
    agent: str = ""
    interrupt_id: str = ""


class InterruptResponse(BaseModel):
    """Returned when the graph pauses for human-in-the-loop approval.

    The client should display the approval requests and send a
    ``POST /chats/{chat_id}/resume`` with the decision.
    """

    chat_id: str
    tenant_id: str
    status: str = "interrupted"
    approvals_needed: list[ToolApprovalRequest]


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
    metadata: dict | None = None


class IndexResponse(BaseModel):
    status: str
    tenant_id: str
    indexed: dict[str, int]


class UploadFileResult(BaseModel):
    """Per-file outcome from ``POST /chats/{id}/upload``.

    Exactly one of ``chunks_indexed`` or ``error`` is populated. The
    filename comes from the request — already sanitised by the upload
    endpoint via ``Path(name).name`` so it never contains directory
    components.
    """

    filename: str
    chunks_indexed: int | None = None
    error: str | None = None


class UploadResponse(BaseModel):
    """Response shape for ``POST /chats/{id}/upload``.

    Always returns 200 even when individual files failed — the per-file
    ``error`` field carries the failure mode so the UI can surface a
    partial success.
    """

    status: str
    files: list[UploadFileResult]


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
        metadata=getattr(m, "metadata", None),
    )
