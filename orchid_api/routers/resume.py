"""Resume endpoint — continue graph execution after HITL approval."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langgraph.errors import GraphInterrupt
from langgraph.types import Command
from pydantic import BaseModel

from orchid_ai.core.state import AuthContext
from orchid_ai.persistence.base import ChatStorage
from orchid_ai.runtime import OrchidRuntime

from ..auth import get_auth_context
from ..context import get_chat_repo, get_graph, get_runtime
from ..models import ChatResponse, InterruptResponse
from ._helpers import build_interrupt_response, verify_chat_ownership

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
    chat_repo: ChatStorage = Depends(get_chat_repo),
    runtime: OrchidRuntime = Depends(get_runtime),
    graph: Any = Depends(get_graph),
):
    """Resume a paused graph after human-in-the-loop approval.

    When the graph interrupts for tool approval, the client calls this
    endpoint with ``{"approved": true}`` or ``{"approved": false}``.
    The graph resumes from the checkpoint and either executes or skips
    the pending tool call.
    """
    await verify_chat_ownership(chat_id, auth, chat_repo)

    if runtime.checkpointer is None:
        raise HTTPException(
            status_code=400,
            detail="Cannot resume: no checkpointer configured. Enable checkpointing to use tool approval.",
        )

    graph_config = {"configurable": {"thread_id": chat_id}}

    try:
        result = await graph.ainvoke(
            Command(resume={"approved": body.approved}),
            config=graph_config,
        )
    except GraphInterrupt as exc:
        return build_interrupt_response(exc, chat_id, auth.tenant_key)

    response_text = result.get("final_response", "No response generated.")
    agents_used = result.get("active_agents", [])

    await chat_repo.add_message(chat_id, "assistant", response_text, agents_used=agents_used)

    return ChatResponse(
        response=response_text,
        chat_id=chat_id,
        tenant_id=auth.tenant_key,
        agents_used=agents_used,
    )
