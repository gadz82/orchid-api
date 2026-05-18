"""Config-hot-reload middleware for ``orchid-api``.

Polls the watcher at most every ``ORCHID_RELOAD_INTERVAL`` seconds
(0 = disabled) and triggers a graph rebuild when config files change.
No background threads — the check runs inline on each request but
respects a per-worker throttle so the stat cost stays bounded.
"""

from __future__ import annotations

import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class ConfigReloadMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that triggers hot-reload at most every *interval_s* seconds.

    Parameters
    ----------
    app : Any
        The ASGI application.
    orchid_ref : Any
        Reference to the :class:`orchid_ai.Orchid` instance.  Must have
        a ``reload_config() -> bool`` method.
    interval_s : float
        Minimum seconds between successive change checks.  Use ``0``
        to disable the middleware entirely (``dispatch`` is a no-op).
    """

    def __init__(
        self,
        app: Any,
        *,
        orchid_ref: Any,
        interval_s: float = 30.0,
    ) -> None:
        super().__init__(app)
        self._orchid_ref = orchid_ref
        self._interval_s = interval_s
        self._last_check: float = 0.0

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if self._interval_s <= 0 or self._orchid_ref is None:
            return await call_next(request)

        now = time.monotonic()
        if now - self._last_check >= self._interval_s:
            self._last_check = now
            try:
                await self._orchid_ref.reload_config()
            except Exception:
                pass  # reload failures are non-fatal — keep serving with previous config

        return await call_next(request)
