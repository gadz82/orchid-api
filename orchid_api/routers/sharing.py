"""Chat sharing endpoint — promotes chat-scoped RAG data to user scope."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from orchid.config.loader import load_config
from orchid.core.state import AuthContext

from ..auth import get_auth_context
from ..context import app_ctx
from ..settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["sharing"])


@router.post("/{chat_id}/share")
async def share_chat(
    chat_id: str,
    auth: AuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
):
    """Promote chat RAG data to user-common scope."""
    if app_ctx.chat_repo is None:
        raise HTTPException(status_code=503, detail="Chat repository not initialised")

    # Sharing requires the Qdrant backend (uses backend-specific filter API).
    try:
        from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

        from orchid.rag.backends.qdrant import QdrantRepository
    except ImportError:
        raise HTTPException(status_code=503, detail="Sharing requires Qdrant backend")

    if not isinstance(app_ctx.reader, QdrantRepository):
        raise HTTPException(status_code=503, detail="Sharing requires Qdrant backend")

    chat = await app_ctx.chat_repo.get_chat(chat_id)
    if not chat or chat.user_id != auth.user_id:
        raise HTTPException(status_code=404, detail="Chat not found")

    # Find all chat-scoped data and duplicate with user scope
    source_filter = Filter(
        must=[
            FieldCondition(key="chat_id", match=MatchValue(value=chat_id)),
            FieldCondition(key="scope", match=MatchAny(any=["chat_shared", "chat_agent"])),
        ]
    )

    # Promote across all RAG namespaces from agent config + uploads
    agents_config = load_config(settings.agents_config_path)
    rag_namespaces = [a.rag.namespace for a in agents_config.agents.values() if a.rag.enabled and a.rag.namespace]
    all_namespaces = list({settings.upload_namespace, *rag_namespaces})

    total_promoted = 0
    for namespace in all_namespaces:
        try:
            count = await app_ctx.reader.promote_scope(
                namespace=namespace,
                source_filter=source_filter,
                new_scope_fields={
                    "scope": "user",
                    "chat_id": None,
                    "agent_id": None,
                    "user_id": auth.user_id,
                    "tenant_id": auth.tenant_key,
                },
            )
            total_promoted += count
        except Exception as exc:
            logger.warning("[Share] Failed for namespace '%s': %s", namespace, exc)

    await app_ctx.chat_repo.mark_shared(chat_id)

    logger.info("[API] /chats/%s/share promoted %d points", chat_id[:8], total_promoted)
    return {"status": "shared", "chat_id": chat_id, "points_promoted": total_promoted}
