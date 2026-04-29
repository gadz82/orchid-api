"""Per-(tenant, user) token-bucket rate limiter.

Lives at the API layer because the framework library has no notion of
HTTP-level abuse — only orchid-api decides "this is one user banging on
expensive endpoints, slow them down". Implementation is in-memory and
process-local; a multi-replica deployment that needs shared limits
would swap the :class:`TokenBucketLimiter` for a Redis-backed variant
(register a constructor that returns the same ``acquire`` contract).

The shared limiter dict is held on :class:`AppContext` (lazy-built on
first access) so identical limit names share one bucket map across
deps that pull the same name.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass

from fastapi import Depends, HTTPException

from orchid_ai.core.state import OrchidAuthContext

from .auth import get_auth_context


@dataclass
class _BucketState:
    """Per-key bucket — current tokens + the moment we last refilled."""

    tokens: float
    last_refill: float


class TokenBucketLimiter:
    """Simple token-bucket limiter with LRU-bounded key storage.

    ``acquire(key)`` returns ``(allowed, retry_after_seconds)``.
    Refills happen on every call, so unused buckets contribute no
    background work. The LRU cap prevents pathological growth when an
    attacker rotates keys; the default of 10 000 distinct keys is well
    below typical Python dict overhead.
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_second: float,
        max_buckets: int = 10_000,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if refill_per_second <= 0:
            raise ValueError("refill_per_second must be > 0")
        self._capacity = float(capacity)
        self._refill = refill_per_second
        self._max_buckets = max_buckets
        self._buckets: OrderedDict[str, _BucketState] = OrderedDict()
        self._lock = asyncio.Lock()

    async def acquire(self, key: str) -> tuple[bool, float]:
        async with self._lock:
            now = time.monotonic()
            state = self._buckets.get(key)
            if state is None:
                while len(self._buckets) >= self._max_buckets:
                    self._buckets.popitem(last=False)
                state = _BucketState(tokens=self._capacity, last_refill=now)
                self._buckets[key] = state
            else:
                self._buckets.move_to_end(key)
                elapsed = now - state.last_refill
                state.tokens = min(self._capacity, state.tokens + elapsed * self._refill)
                state.last_refill = now

            if state.tokens >= 1.0:
                state.tokens -= 1.0
                return True, 0.0

            deficit = 1.0 - state.tokens
            wait = deficit / self._refill
            return False, wait


def rate_limit(name: str, *, calls: int, period: float):
    """Build a FastAPI dependency that token-buckets per ``(tenant, user)``.

    The bucket is created once at decoration time and shared across all
    requests that hit the dependency. Per ``(tenant, user)`` keys live
    inside the bucket; a different ``name`` (e.g. ``"upload"`` vs
    ``"message"``) gets its own bucket so a noisy uploader doesn't
    starve out chat traffic.

    Setting ``calls<=0`` returns a no-op dependency — useful for tests
    or when an operator wants to disable a specific bucket via the
    settings file.
    """
    if calls <= 0:

        async def _noop(auth: OrchidAuthContext = Depends(get_auth_context)) -> OrchidAuthContext:
            return auth

        return _noop

    bucket = TokenBucketLimiter(
        capacity=calls,
        refill_per_second=calls / period,
    )

    async def _enforce(auth: OrchidAuthContext = Depends(get_auth_context)) -> OrchidAuthContext:
        key = f"{name}:{auth.tenant_key}:{auth.user_id}"
        allowed, retry_after = await bucket.acquire(key)
        if not allowed:
            seconds = max(1, int(retry_after) + 1)
            raise HTTPException(
                status_code=429,
                detail=(f"Rate limit exceeded for `{name}` ({calls}/{int(period)}s per user). Retry in ~{seconds}s."),
                headers={"Retry-After": str(seconds)},
            )
        return auth

    return _enforce
