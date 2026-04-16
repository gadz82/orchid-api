"""Resume endpoint — continue graph execution after HITL approval."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from langgraph.types import Command
from pydantic import BaseModel

from orchid_ai.core.state import AuthContext

from ..auth import get_auth_context
from ..context import app_ctx
from ..models import ChatResponse, InterruptResponse, ToolApprovalRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["resume"])


class ResumeRequest(BaseModel):
    """Client decision on a tool approval interrupt."""

    approved: bool = True


@router.post("/{chat_id}/resume", response_model=ChatResponse | InterruptResponse)
async def resume_chat(
    chat_id: str,
    body: ResumeRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    """Resume a paused graph after human-in-the-loop approval.

    When the graph interrupts for tool approval, the client calls this
    endpoint with ``{"approved": true}`` or ``{"approved": false}``.
    The graph resumes from the checkpoint and either executes or skips
    the pending tool call.
    """
    if app_ctx.graph is None:
        raise HTTPException(status_code=503, detail="Graph not initialised")
    if app_ctx.chat_repo is None:
        raise HTTPException(status_code=503, detail="Chat repository not initialised")

    # Verify chat ownership
    chat = await app_ctx.chat_repo.get_chat(chat_id)
    if not chat or chat.user_id != auth.user_id:
        raise HTTPException(status_code=404, detail="Chat not found")

    # Checkpointer is required for resume
    if app_ctx.runtime.checkpointer is None:
        raise HTTPException(
            status_code=400,
            detail="Cannot resume: no checkpointer configured. Enable checkpointing to use tool approval.",
        )

    graph_config = {"configurable": {"thread_id": chat_id}}

    try:
        result = await app_ctx.graph.ainvoke(
            Command(resume={"approved": body.approved}),
            config=graph_config,
        )
    except Exception as exc:
        # Another interrupt (multi-step approval chain)
        if type(exc).__name__ == "GraphInterrupt":
            interrupts = exc.args[0] if exc.args else []
            approvals = [
                ToolApprovalRequest(
                    tool=i.value.get("tool", "") if isinstance(i.value, dict) else str(i.value),
                    args=i.value.get("args", {}) if isinstance(i.value, dict) else {},
                    agent=i.value.get("agent", "") if isinstance(i.value, dict) else "",
                    interrupt_id=str(i.id),
                )
                for i in interrupts
            ]
            return InterruptResponse(
                chat_id=chat_id,
                tenant_id=auth.tenant_key,
                approvals_needed=approvals,
            )
        raise

    response_text = result.get("final_response", "No response generated.")
    agents_used = result.get("active_agents", [])

    # Persist messages now that the graph completed normally
    # Note: the original user message was already persisted before the interrupt
    await app_ctx.chat_repo.add_message(chat_id, "assistant", response_text, agents_used=agents_used)

    return ChatResponse(
        response=response_text,
        chat_id=chat_id,
        tenant_id=auth.tenant_key,
        agents_used=agents_used,
    )
