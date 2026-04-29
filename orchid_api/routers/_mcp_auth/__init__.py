"""Internal split of the MCP per-server OAuth router.

The public surface lives in :mod:`orchid_api.routers.mcp_auth`. The
sub-modules here split the handlers by domain responsibility so each
file stays under ~150 lines:

  - :mod:`_helpers` — PKCE generators, callback URL, HTML page
                      builders, and the token-exchange helper.
  - :mod:`discovery` — list servers + force discovery.
  - :mod:`authorize` — generate authorization URL.
  - :mod:`callback` — OAuth IdP redirect handler.
  - :mod:`revoke` — delete a stored token.
"""
