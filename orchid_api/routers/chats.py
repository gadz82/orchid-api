"""Chat CRUD endpoints — create, list, get messages, delete."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from orchid_ai.core.state import AuthContext

from ..auth import get_auth_context
from ..context import app_ctx
from ..models import ChatSessionOut, CreateChatRequest, MessageOut, message_to_out, session_to_out

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])


@router.post("", response_model=ChatSessionOut)
async def create_chat(
    request: CreateChatRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    """Create a new chat session."""
    if app_ctx.chat_repo is None:
        raise HTTPException(status_code=503, detail="Chat repository not initialised")

    session = await app_ctx.chat_repo.create_chat(
        tenant_id=auth.tenant_key,
        user_id=auth.user_id,
        title=request.title or "New chat",
    )
    return session_to_out(session)


@router.get("", response_model=list[ChatSessionOut])
async def list_chats(
    auth: AuthContext = Depends(get_auth_context),
):
    """List all chat sessions for the current user."""
    if app_ctx.chat_repo is None:
        raise HTTPException(status_code=503, detail="Chat repository not initialised")

    sessions = await app_ctx.chat_repo.list_chats(
        tenant_id=auth.tenant_key,
        user_id=auth.user_id,
    )
    return [session_to_out(s) for s in sessions]


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
async def get_messages(
    chat_id: str,
    limit: int = 50,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
):
    """Load message history for a chat."""
    if app_ctx.chat_repo is None:
        raise HTTPException(status_code=503, detail="Chat repository not initialised")

    chat = await app_ctx.chat_repo.get_chat(chat_id)
    if not chat or chat.user_id != auth.user_id:
        raise HTTPException(status_code=404, detail="Chat not found")

    messages = await app_ctx.chat_repo.get_messages(chat_id, limit=limit, offset=offset)
    return [message_to_out(m) for m in messages]


@router.delete("/{chat_id}")
async def delete_chat(
    chat_id: str,
    auth: AuthContext = Depends(get_auth_context),
):
    """Delete a chat session and all its messages."""
    if app_ctx.chat_repo is None:
        raise HTTPException(status_code=503, detail="Chat repository not initialised")

    chat = await app_ctx.chat_repo.get_chat(chat_id)
    if not chat or chat.user_id != auth.user_id:
        raise HTTPException(status_code=404, detail="Chat not found")

    await app_ctx.chat_repo.delete_chat(chat_id)
    return {"status": "deleted", "chat_id": chat_id}
