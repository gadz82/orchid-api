"""
LangSmith tracing configuration — full visibility into every agent step.

Called once during FastAPI lifespan (in main.py).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def configure_tracing(*, enabled: bool, api_key: str, project: str = "orchid") -> None:
    """Configure LangSmith tracing for both LangGraph and LiteLLM."""
    if not enabled or not api_key:
        os.environ.pop("LANGCHAIN_TRACING_V2", None)
        logger.info("[Tracing] LangSmith tracing DISABLED")
        return

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = api_key
    os.environ["LANGCHAIN_PROJECT"] = project

    try:
        import litellm

        litellm.success_callback = litellm.success_callback or []
        litellm.failure_callback = litellm.failure_callback or []

        if "langsmith" not in litellm.success_callback:
            litellm.success_callback.append("langsmith")
        if "langsmith" not in litellm.failure_callback:
            litellm.failure_callback.append("langsmith")

        logger.info(
            "[Tracing] LangSmith tracing ENABLED — project=%s, LangGraph=auto, LiteLLM=callback",
            project,
        )
    except Exception as exc:
        logger.warning(
            "[Tracing] LangGraph tracing enabled, but LiteLLM callback setup failed: %s",
            exc,
        )
