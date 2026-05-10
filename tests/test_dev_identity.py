"""Unit tests for DevBypassIdentityResolver.

Verifies all three resolver methods return the expected
OrchidAuthContext shapes without any network calls.
"""

from __future__ import annotations

import pytest

from orchid_api.dev_identity import DevBypassIdentityResolver


@pytest.mark.asyncio
async def test_resolve_returns_hardcoded_dev_context():
    resolver = DevBypassIdentityResolver()
    ctx = await resolver.resolve(domain="any.example.com", bearer_token="ignored")
    assert ctx.access_token == "dev-token"
    assert ctx.tenant_key == "99999"
    assert ctx.user_id == "dev-user-00000000"


@pytest.mark.asyncio
async def test_resolve_service_account_embeds_name_in_user_id():
    resolver = DevBypassIdentityResolver()
    ctx = await resolver.resolve_service_account(name="digest-bot")
    assert ctx.access_token == "dev-token"
    assert ctx.tenant_key == "99999"
    assert ctx.user_id == "svc:digest-bot"


@pytest.mark.asyncio
async def test_mint_for_user_uses_caller_supplied_ids():
    resolver = DevBypassIdentityResolver()
    ctx = await resolver.mint_for_user(tenant_key="t-42", user_id="u-99")
    assert ctx.access_token == "dev-token"
    assert ctx.tenant_key == "t-42"
    assert ctx.user_id == "u-99"


@pytest.mark.asyncio
async def test_mint_for_user_different_tenants_are_independent():
    resolver = DevBypassIdentityResolver()
    a = await resolver.mint_for_user(tenant_key="ta", user_id="ua")
    b = await resolver.mint_for_user(tenant_key="tb", user_id="ub")
    assert a.tenant_key != b.tenant_key
    assert a.user_id != b.user_id
