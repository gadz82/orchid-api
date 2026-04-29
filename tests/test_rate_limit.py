"""Unit tests for ``orchid_api.rate_limit``."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from orchid_ai.core.state import OrchidAuthContext

from orchid_api.auth import get_auth_context
from orchid_api.rate_limit import TokenBucketLimiter, rate_limit


@pytest.mark.asyncio
async def test_token_bucket_allows_up_to_capacity():
    bucket = TokenBucketLimiter(capacity=3, refill_per_second=1.0)
    for _ in range(3):
        allowed, _ = await bucket.acquire("user-1")
        assert allowed is True


@pytest.mark.asyncio
async def test_token_bucket_rejects_after_capacity():
    bucket = TokenBucketLimiter(capacity=2, refill_per_second=1.0)
    await bucket.acquire("user-1")
    await bucket.acquire("user-1")
    allowed, retry_after = await bucket.acquire("user-1")
    assert allowed is False
    assert retry_after > 0


@pytest.mark.asyncio
async def test_token_bucket_refills_over_time():
    bucket = TokenBucketLimiter(capacity=1, refill_per_second=10.0)
    await bucket.acquire("user-1")
    rejected_allowed, _ = await bucket.acquire("user-1")
    assert rejected_allowed is False
    await asyncio.sleep(0.15)
    allowed, _ = await bucket.acquire("user-1")
    assert allowed is True


@pytest.mark.asyncio
async def test_token_bucket_isolates_keys():
    """One noisy user does not starve another."""
    bucket = TokenBucketLimiter(capacity=1, refill_per_second=0.1)
    await bucket.acquire("user-1")
    rejected, _ = await bucket.acquire("user-1")
    assert rejected is False

    other_allowed, _ = await bucket.acquire("user-2")
    assert other_allowed is True


@pytest.mark.asyncio
async def test_token_bucket_evicts_oldest_when_full():
    """Pathological key rotation must not grow the bucket dict unboundedly."""
    bucket = TokenBucketLimiter(capacity=1, refill_per_second=1.0, max_buckets=3)
    for i in range(10):
        await bucket.acquire(f"user-{i}")
    # Internal state — only the last 3 keys remain.
    assert len(bucket._buckets) == 3  # type: ignore[attr-defined]


def test_rate_limit_dependency_returns_429_when_exhausted():
    """End-to-end: third request in a row hits the bucket limit and 429s."""
    app = FastAPI()
    limiter = rate_limit("test", calls=2, period=60.0)

    @app.get("/probe", dependencies=[__import__("fastapi").Depends(limiter)])
    async def probe() -> dict:
        return {"ok": True}

    app.dependency_overrides[get_auth_context] = lambda: OrchidAuthContext(
        access_token="t", tenant_key="t1", user_id="u1"
    )
    client = TestClient(app)

    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 200
    third = client.get("/probe")
    assert third.status_code == 429
    assert "Retry-After" in third.headers
    assert third.json()["detail"].startswith("Rate limit exceeded")


def test_rate_limit_disabled_when_calls_zero():
    """``calls=0`` returns a no-op dep — useful for the disable-via-settings path."""
    app = FastAPI()
    limiter = rate_limit("test", calls=0, period=60.0)

    @app.get("/probe", dependencies=[__import__("fastapi").Depends(limiter)])
    async def probe() -> dict:
        return {"ok": True}

    app.dependency_overrides[get_auth_context] = lambda: OrchidAuthContext(
        access_token="t", tenant_key="t1", user_id="u1"
    )
    client = TestClient(app)

    for _ in range(20):
        assert client.get("/probe").status_code == 200


def test_rate_limit_isolates_per_user():
    """Two users sharing one endpoint do not deplete each other's bucket."""
    app = FastAPI()
    limiter = rate_limit("test", calls=1, period=60.0)

    current_user = {"id": "u1"}

    @app.get("/probe", dependencies=[__import__("fastapi").Depends(limiter)])
    async def probe() -> dict:
        return {"ok": True}

    app.dependency_overrides[get_auth_context] = lambda: OrchidAuthContext(
        access_token="t", tenant_key="t1", user_id=current_user["id"]
    )
    client = TestClient(app)

    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 429
    current_user["id"] = "u2"
    assert client.get("/probe").status_code == 200


def test_token_bucket_rejects_invalid_construction():
    with pytest.raises(ValueError):
        TokenBucketLimiter(capacity=0, refill_per_second=1.0)
    with pytest.raises(ValueError):
        TokenBucketLimiter(capacity=1, refill_per_second=0.0)


def test_rate_limit_429_carries_retry_after_header():
    """Clients honoring RFC 7231 Retry-After need an integer second count."""
    app = FastAPI()
    limiter = rate_limit("test", calls=1, period=60.0)

    @app.get("/probe", dependencies=[__import__("fastapi").Depends(limiter)])
    async def probe() -> dict:
        return {"ok": True}

    app.dependency_overrides[get_auth_context] = lambda: OrchidAuthContext(
        access_token="t", tenant_key="t1", user_id="u1"
    )
    client = TestClient(app)

    client.get("/probe")
    rejected = client.get("/probe")
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"].isdigit()
    assert int(rejected.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_rate_limit_dependency_raises_http_exception_on_reject():
    """Direct invocation (no FastAPI request lifecycle) still raises 429."""
    auth = OrchidAuthContext(access_token="t", tenant_key="t1", user_id="u1")
    limiter = rate_limit("direct", calls=1, period=60.0)
    # First call succeeds.
    await limiter(auth=auth)
    # Second hits the cap.
    with pytest.raises(HTTPException) as exc_info:
        await limiter(auth=auth)
    assert exc_info.value.status_code == 429
