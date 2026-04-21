"""Chat CRUD endpoints — create, list, get messages, delete."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from orchid_ai.core.state import OrchidAuthContext
from orchid_ai.persistence.base import OrchidChatStorage

from ..auth import get_auth_context
from ..context import get_chat_repo
from ..models import ChatSessionOut, CreateChatRequest, MessageOut, message_to_out, session_to_out

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])


@router.post("", response_model=ChatSessionOut)
async def create_chat(
    request: CreateChatRequest,
    auth: OrchidAuthContext = Depends(get_auth_context),
    chat_repo: OrchidChatStorage = Depends(get_chat_repo),
):
    """Create a new chat session."""
    session = await chat_repo.create_chat(
        tenant_id=auth.tenant_key,
        user_id=auth.user_id,
        title=request.title or "New chat",
    )
    return session_to_out(session)


@router.get("", response_model=list[ChatSessionOut])
async def list_chats(
    auth: OrchidAuthContext = Depends(get_auth_context),
    chat_repo: OrchidChatStorage = Depends(get_chat_repo),
):
    """List all chat sessions for the current user."""
    sessions = await chat_repo.list_chats(
        tenant_id=auth.tenant_key,
        user_id=auth.user_id,
    )
    return [session_to_out(s) for s in sessions]


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
async def get_messages(
    chat_id: str,
    limit: int = 50,
    offset: int = 0,
    auth: OrchidAuthContext = Depends(get_auth_context),
    chat_repo: OrchidChatStorage = Depends(get_chat_repo),
):
    """Load message history for a chat."""
    chat = await chat_repo.get_chat(chat_id)
    if not chat or chat.user_id != auth.user_id:
        raise HTTPException(status_code=404, detail="Chat not found")

    messages = await chat_repo.get_messages(chat_id, limit=limit, offset=offset)
    return [message_to_out(m) for m in messages]


@router.delete("/{chat_id}")
async def delete_chat(
    chat_id: str,
    auth: OrchidAuthContext = Depends(get_auth_context),
    chat_repo: OrchidChatStorage = Depends(get_chat_repo),
):
    """Delete a chat session and all its messages."""
    chat = await chat_repo.get_chat(chat_id)
    if not chat or chat.user_id != auth.user_id:
        raise HTTPException(status_code=404, detail="Chat not found")

    await chat_repo.delete_chat(chat_id)
    return {"status": "deleted", "chat_id": chat_id}
