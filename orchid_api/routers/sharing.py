"""Chat sharing endpoint — promotes chat-scoped RAG data to user scope."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from orchid_ai.config.schema import AgentsConfig
from orchid_ai.core.repository import VectorStoreRepository
from orchid_ai.core.state import AuthContext
from orchid_ai.persistence.base import ChatStorage
from orchid_ai.runtime import OrchidRuntime

from ..auth import get_auth_context
from ..context import get_agents_config, get_chat_repo, get_runtime
from ..settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["sharing"])


@router.post("/{chat_id}/share")
async def share_chat(
    chat_id: str,
    auth: AuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
    chat_repo: ChatStorage = Depends(get_chat_repo),
    runtime: OrchidRuntime = Depends(get_runtime),
    agents_config: AgentsConfig = Depends(get_agents_config),
):
    """Promote chat RAG data to user-common scope.

    Requires a :class:`VectorStoreRepository` whose
    ``supports_scope_promotion`` flag is ``True`` (today only Qdrant).
    Returns **501 Not Implemented** when the backend exists but doesn't
    support promotion — the route is present, the action is not.
    """
    reader = runtime.get_reader()
    if not isinstance(reader, VectorStoreRepository) or not reader.supports_scope_promotion:
        raise HTTPException(
            status_code=501,
            detail="Sharing is not supported by the configured vector backend.",
        )

    # Backend-specific filter API — lazy-imported to keep qdrant-client an
    # optional dep of the API package.  The capability check above ensures
    # Qdrant is the active backend when we reach this point.
    try:
        from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue
    except ImportError:
        raise HTTPException(status_code=503, detail="Sharing requires qdrant-client to be installed")

    chat = await chat_repo.get_chat(chat_id)
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
    rag_namespaces = [a.rag.namespace for a in agents_config.agents.values() if a.rag.enabled and a.rag.namespace]
    all_namespaces = list({settings.upload_namespace, *rag_namespaces})

    total_promoted = 0
    for namespace in all_namespaces:
        try:
            count = await reader.promote_scope(
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

    await chat_repo.mark_shared(chat_id)

    logger.info("[API] /chats/%s/share promoted %d points", chat_id[:8], total_promoted)
    return {"status": "shared", "chat_id": chat_id, "points_promoted": total_promoted}
