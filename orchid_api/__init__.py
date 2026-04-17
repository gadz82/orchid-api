"""Orchid API — FastAPI server for the Orchid multi-agent framework.

Public surface for integrators who want to embed orchid-api into their
own FastAPI app (instead of running ``orchid_api.main:app`` standalone)::

    from contextlib import asynccontextmanager
    from fastapi import FastAPI

    from orchid_api import setup_orchid, teardown_orchid
    from orchid_api.routers import chats, messages, streaming, resume

    @asynccontextmanager
    async def lifespan(app):
        await setup_orchid()
        yield
        await teardown_orchid()

    app = FastAPI(lifespan=lifespan)
    app.include_router(chats.router,     prefix="/ai")
    app.include_router(messages.router,  prefix="/ai")
    app.include_router(streaming.router, prefix="/ai")
    app.include_router(resume.router,    prefix="/ai")
"""

from __future__ import annotations

from .context import app_ctx
from .lifecycle import setup_orchid, teardown_orchid

__all__ = [
    "app_ctx",
    "setup_orchid",
    "teardown_orchid",
]
