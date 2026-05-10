"""Dev-bypass identity resolver — used when ``DEV_AUTH_BYPASS=true`` and no
real ``IDENTITY_RESOLVER_CLASS`` is configured.

This resolver trusts ALL inputs — it never validates tokens, never calls an
IdP, and returns a fabricated :class:`~orchid_ai.core.state.OrchidAuthContext`
based on whatever identifiers it receives.

**MUST NEVER be used in production.**  It exists solely so that the events
processor can materialise auth for ``act_as_user`` Bloom triggers when running
under ``DEV_AUTH_BYPASS=true`` (e.g. local Docker-Compose demos where there is
no real identity provider).
"""

from __future__ import annotations

import logging

from orchid_ai.core.identity import OrchidIdentityResolver
from orchid_ai.core.state import OrchidAuthContext

_logger = logging.getLogger(__name__)

_DEV_TOKEN = "dev-token"
_DEV_TENANT = "99999"
_DEV_USER = "dev-user-00000000"


class DevBypassIdentityResolver(OrchidIdentityResolver):
    """Identity resolver that blindly trusts its inputs — for local dev only."""

    async def resolve(self, domain: str, bearer_token: str) -> OrchidAuthContext:
        _logger.debug("[DevBypass] resolve called — returning hardcoded dev context")
        return OrchidAuthContext(
            access_token=_DEV_TOKEN,
            tenant_key=_DEV_TENANT,
            user_id=_DEV_USER,
        )

    async def resolve_service_account(self, name: str) -> OrchidAuthContext:
        return OrchidAuthContext(
            access_token=_DEV_TOKEN,
            tenant_key=_DEV_TENANT,
            user_id=f"svc:{name}",
        )

    async def mint_for_user(self, tenant_key: str, user_id: str) -> OrchidAuthContext:
        """Return a dev context scoped to the exact tenant+user the signal carries."""
        _logger.debug(
            "[DevBypass] mint_for_user called — tenant=%s user=%s",
            tenant_key,
            user_id,
        )
        return OrchidAuthContext(
            access_token=_DEV_TOKEN,
            tenant_key=tenant_key,
            user_id=user_id,
        )
