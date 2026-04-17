# CHANGELOG

<!-- version list -->

## v1.1.0 (2026-04-17)

### Bug Fixes

- Buffer agent results for done event, improve handoff detection
  ([`a0016a1`](https://github.com/gadz82/orchid-api/commit/a0016a11171b74e157b82a8c82db1cd4e0408cc7))

- Deduplicate handoff messages and strip LLM preamble
  ([`bef441b`](https://github.com/gadz82/orchid-api/commit/bef441b2f24d582f6ebbd8c3fb34ab2e1e090089))

- Emit handoff messages as separate SSE event type
  ([`0e5953d`](https://github.com/gadz82/orchid-api/commit/0e5953d80b8305a7031ef2466fa5dbcb90395012))

- Filter streaming to only emit synthesis tokens, deduplicate
  ([`588ec16`](https://github.com/gadz82/orchid-api/commit/588ec163fd6ca48d170f7add215e3c8a719c51bf))

- Prevent handoff text from leaking into response bubble
  ([`03795a6`](https://github.com/gadz82/orchid-api/commit/03795a6ed3affe336103487fdedc1d30ccfd3050))

### Continuous Integration

- Grant pull-requests: write permission to the test job
  ([`a819a3d`](https://github.com/gadz82/orchid-api/commit/a819a3dbda5d65106cf8496807c0b6996827da0e))

### Features

- Add human-in-the-loop (HITL) tool approval workflow
  ([`8511260`](https://github.com/gadz82/orchid-api/commit/85112608706e60ff46baf8ff93865b88470934ad))

- Add LangGraph checkpointer integration for state persistence
  ([`404c547`](https://github.com/gadz82/orchid-api/commit/404c547ff89d1c72178f5c7d0e86981429742295))

- Add resume endpoint to support HITL tool approval
  ([`5823cbf`](https://github.com/gadz82/orchid-api/commit/5823cbfd61f3fb74b28479a36306e6da3d71ace6))

- Add SSE streaming endpoint for real-time responses
  ([`5ec8141`](https://github.com/gadz82/orchid-api/commit/5ec8141ded29110eaf28217882449294426a2fdd))

- Emit agent done status with preview (parallel + sequential)
  ([`70fdbb7`](https://github.com/gadz82/orchid-api/commit/70fdbb794842a5e6c3d297ec1b31ed9e38138bc7))

- Emit agent_result events with agent response content
  ([`98a5536`](https://github.com/gadz82/orchid-api/commit/98a5536e8ccb2f0b8237c4cb93749ad2395c78eb))

- Optimise message handling with checkpointer-aware invocation
  ([`7851e71`](https://github.com/gadz82/orchid-api/commit/7851e71f47cb045e1fadce5dce378909c89ce3d8))

- Upgrade orchid-ai dependency to >=1.3.0
  ([`c20ba54`](https://github.com/gadz82/orchid-api/commit/c20ba54e6981febe02ba364c7387f815d9fd1c28))

- **api**: Refactor lifecycle for modular integration
  ([`5a7baef`](https://github.com/gadz82/orchid-api/commit/5a7baef818a71f495d8ffd015ebc7c448a204bde))

- **api**: Refactor sharing tests + MCP auth dependency injection
  ([`3a07c6f`](https://github.com/gadz82/orchid-api/commit/3a07c6fe053e2f2daeda6cb3725de2215c48ba23))

- **streaming**: Buffer supervisor tokens for handoff classification
  ([`c7373e3`](https://github.com/gadz82/orchid-api/commit/c7373e31af7b03da7feb729d6812f3f7f65b61a1))

### Refactoring

- Centralize helpers and streamline PKCE OAuth flow
  ([`6c6ccfe`](https://github.com/gadz82/orchid-api/commit/6c6ccfe82d3d2c36dbdb6e8eae7c0dfdfbdb0493))

### Testing

- Add HITL tool approval tests
  ([`5823cbf`](https://github.com/gadz82/orchid-api/commit/5823cbfd61f3fb74b28479a36306e6da3d71ace6))


## v1.0.11 (2026-04-15)

### Bug Fixes

- Add MCP pre-flight auth check and expose auth-required servers
  ([`b73c458`](https://github.com/gadz82/orchid-api/commit/b73c45860f30293fc22e708f6f867e468101e136))

- Bump orchid-ai dependency to v1.2.14 in CLI and API
  ([`9a54d23`](https://github.com/gadz82/orchid-api/commit/9a54d2308ec66817645644491ad92120fadfea63))

- MCP OAuth flow with server list, authorization, callback, and token revocation endpoints
  ([`ab02f49`](https://github.com/gadz82/orchid-api/commit/ab02f4971c4fc3744b7a942a120a6ccbcad96a28))

- MCP OAuth token store management and API base URL configuration
  ([`6c8d8d3`](https://github.com/gadz82/orchid-api/commit/6c8d8d3ce550fd74496140932a87a8ae6b7ad0b6))

- Simplify HTML content formatting and remove unused imports in mcp_auth and cli commands
  ([`55748a3`](https://github.com/gadz82/orchid-api/commit/55748a33575256daa91f9710eac96c1e28bca910))


## v1.0.10 (2026-04-14)

### Bug Fixes

- Bump orchid-ai dependency to 1.2.13
  ([`ae53e47`](https://github.com/gadz82/orchid-api/commit/ae53e47bd9eca865e8c3606ca535d12a1ffbadb4))


## v1.0.9 (2026-04-14)

### Bug Fixes

- Implement multi-turn LLM tool loop package version update
  ([`c9598c8`](https://github.com/gadz82/orchid-api/commit/c9598c8594eb98749938eb0bf42e61c4a58182c8))


## v1.0.8 (2026-04-14)

### Bug Fixes

- Improve LLM-driven tool execution
  ([`dc8f1dc`](https://github.com/gadz82/orchid-api/commit/dc8f1dcb6ef94c493165c77342bde47ca498c24f))


## v1.0.7 (2026-04-14)

### Bug Fixes

- Built-in tools args propagation fix.
  ([`68e6d8a`](https://github.com/gadz82/orchid-api/commit/68e6d8ad243ac2d074b6f12e20e1209cceab3ffe))


## v1.0.6 (2026-04-14)

### Bug Fixes

- Built-in tools args propagation fix.
  ([`16fcc74`](https://github.com/gadz82/orchid-api/commit/16fcc74bf685ca5e1ab867aac86be57911ddca95))


## v1.0.5 (2026-04-14)

### Bug Fixes

- Built-in tools auth context propagation.
  ([`e7b86d4`](https://github.com/gadz82/orchid-api/commit/e7b86d4e9eeaae4bfa8c10c9cba76043a48bd423))


## v1.0.4 (2026-04-14)

### Bug Fixes

- Built-in tools parameter declarations in config.
  ([`e804dc1`](https://github.com/gadz82/orchid-api/commit/e804dc158bba43b7956bd8e17236d5a5a03f0f3a))


## v1.0.3 (2026-04-13)

### Bug Fixes

- Tools result context injection.
  ([`f69a951`](https://github.com/gadz82/orchid-api/commit/f69a951fffa248dac9a5f9b408cc3c5dcbc8f2a3))


## v1.0.2 (2026-04-13)

### Bug Fixes

- Coversation context optimization.
  ([`ee10a38`](https://github.com/gadz82/orchid-api/commit/ee10a384c0ae6e12cd7d3d20b2cd017f3c354c7c))


## v1.0.0 (2026-04-13)

- Initial Release

## v1.0.1 (2026-04-10)

### Bug Fixes

- Remove useless Dockerfiles for orchid and orchid-api
  ([`e0a61ba`](https://github.com/gadz82/orchid-api/commit/e0a61ba21abda65011b0f7ea3337eb90238246e2))


## v1.0.0 (2026-04-10)

- Initial Release
