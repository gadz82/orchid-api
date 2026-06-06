# CHANGELOG

<!-- version list -->

## v1.8.6 (2026-06-06)

### Bug Fixes

- Implement local handling of `api` config section in `orchid.yml` and add related tests
  ([`1b224ed`](https://github.com/gadz82/orchid-api/commit/1b224ed19f7a785213b29f7efd6e6aed407db4d8))

- Relax test to handle temporary presence of deprecated `api` section in `orchid.yml`
  ([`0df29d6`](https://github.com/gadz82/orchid-api/commit/0df29d6364351d805ca8d8e2efeb1a8d64cc3bb0))


## v1.8.5 (2026-06-05)

### Bug Fixes

- Integrate `with_auth` into graph configurations and update impacted modules
  ([`75d7e82`](https://github.com/gadz82/orchid-api/commit/75d7e82878ac1493d182fa76fd2c864a0040dbcb))

- Update orchid-ai dependency to version 1.8.8
  ([`007d723`](https://github.com/gadz82/orchid-api/commit/007d723c9d2a676842cd33d74fe231093173045c))

- Update orchid-ai dependency to version 1.8.8
  ([`d36b4a7`](https://github.com/gadz82/orchid-api/commit/d36b4a7cce3d69927e05b9de7bd86b816d5276be))


## v1.8.4 (2026-06-04)

### Bug Fixes

- Bump orchid-ai dependency to 1.8.7 in pyproject.toml
  ([`c1973f9`](https://github.com/gadz82/orchid-api/commit/c1973f97302ae1091962b1aacbd0f0aa31bceb04))


## v1.8.3 (2026-06-03)

### Bug Fixes

- Handle idle chat event streams gracefully by returning idle responses
  ([`f6f83bc`](https://github.com/gadz82/orchid-api/commit/f6f83bc803e08b920bd2da927052ac568825c6bb))


## v1.8.2 (2026-06-01)

### Bug Fixes

- Update orchid-ai dependency to version 1.8.6 across all modules
  ([`5c3ba1d`](https://github.com/gadz82/orchid-api/commit/5c3ba1d182aa1193a3eb12020a41e514820870c7))


## v1.8.1 (2026-05-29)

### Bug Fixes

- Add `orchid-ai` heavy extras `[documents,events]` to dependencies for runtime support
  ([`ef469fe`](https://github.com/gadz82/orchid-api/commit/ef469fe12c6e52b02d0b6eda8c3c0865195e2663))


## v1.8.0 (2026-05-27)

### Bug Fixes

- Add `_mock_qdrant` fixture and update sharing route tests
  ([`7e003e5`](https://github.com/gadz82/orchid-api/commit/7e003e56f82a91f91c8fd496ce5ac0487628da6c))

- Add `upload_namespace` handling and enable signal-based content delegation
  ([`c908f89`](https://github.com/gadz82/orchid-api/commit/c908f89d10648a18c267a4d973d98a834aefee10))

- Add dependency matrix documentation across Orchid modules
  ([`14f2534`](https://github.com/gadz82/orchid-api/commit/14f25348cc8a2b2481c972189ea7bd6e9d05e761))

- Correct class path for PostgreSQL storage in orchid.yml example
  ([`c6a3ab8`](https://github.com/gadz82/orchid-api/commit/c6a3ab8e244fd45d9f6ed8cf684e964cf5b034b4))

- Handle tenant isolation in chat/message operations and improve error persistence
  ([`7a1d86c`](https://github.com/gadz82/orchid-api/commit/7a1d86c2c75871fc97fb71a6bb5828719c82d2d3))

- Replace `asyncio.get_event_loop` with `asyncio.run` in test_event_routers
  ([`b94671e`](https://github.com/gadz82/orchid-api/commit/b94671e2139a5eb1dc4f692140489e191bb99340))

- Replace raw JSONResponse with ChatResponse for MCP compatibility
  ([`a290894`](https://github.com/gadz82/orchid-api/commit/a290894aecd04f5fc6d6192aa2193704d395a3eb))

### Features

- Serve export files via static route
  ([`15a7406`](https://github.com/gadz82/orchid-api/commit/15a740681a051420d4481f6555c02bbe1f0cbf10))


## v1.7.2 (2026-05-22)

### Bug Fixes

- Update orchid-ai dependency to version 1.8.2
  ([`8bd9cfa`](https://github.com/gadz82/orchid-api/commit/8bd9cfaf5830e56ca3b4f10ed61cdd6d6455a48d))

- Upgrade orchid-ai to v1.8.1 and add content_sources_json config
  ([`7f818b2`](https://github.com/gadz82/orchid-api/commit/7f818b23471f9f5e3a075ed898f81ecd2db37e85))


## v1.7.1 (2026-05-19)

### Bug Fixes

- Identity resolvers http_client conditional parameter
  ([`e0d8b98`](https://github.com/gadz82/orchid-api/commit/e0d8b985defffa6e712487630e5bce8ac69b1c05))


## v1.7.0 (2026-05-18)

### Bug Fixes

- Remove redundant CLAUDE.md symlinks across modules [skip ci]
  ([`7441c5a`](https://github.com/gadz82/orchid-api/commit/7441c5acfd8b4159e5c803e0d170c16528492f95))

### Features

- Add Markdown config support, hot-reload middleware, and MD integration tests
  ([`612079a`](https://github.com/gadz82/orchid-api/commit/612079acee7baef4959508763343d0a56ea46186))

- Update orchid-ai dependency to version 1.7.4
  ([`6f38e69`](https://github.com/gadz82/orchid-api/commit/6f38e69b2c183f6faf67ae4d5ec58fa385c506c2))

- **streaming**: Implement partial response persistence for cancellations
  ([`1cda029`](https://github.com/gadz82/orchid-api/commit/1cda02919750cd0a58b20143b94fc7bca9a3aad0))


## v1.6.1 (2026-05-13)

### Bug Fixes

- Add links to orchid-examples in READMEs across projects. [skip_ci]
  ([`ac8ad22`](https://github.com/gadz82/orchid-api/commit/ac8ad2298efd96a132493fb396e114ca3c8de786))


## v1.6.0 (2026-05-10)

### Bug Fixes

- Test imports error
  ([`6635739`](https://github.com/gadz82/orchid-api/commit/663573972d7eb0748929416cf47800d99017d275))

### Chores

- Bump orchid-ai dependency to v1.7.0
  ([`fd81d75`](https://github.com/gadz82/orchid-api/commit/fd81d75885e8390f4c1db9b888ab06aa9994a96e))

### Documentation

- Add Pollen and Bloom operator panel, in-chat progress, and CLI tools
  ([`a6cdebe`](https://github.com/gadz82/orchid-api/commit/a6cdebe0a4bb380158cae628bd6c39074a5d63fc))

### Features

- Add lifecycle tests for DevBypassIdentityResolver and `_build_graph_invoker`
  ([`836c997`](https://github.com/gadz82/orchid-api/commit/836c997e3a911bb881de126dc97373c2d6d24f40))

- Implement bloom event that can create a new chat for a specific user. DevBypassIdentityResolver
  and LangGraph invoker setup.
  ([`8380f25`](https://github.com/gadz82/orchid-api/commit/8380f255d69444f1374423c09d0da42be4d861b4))

- Implement Pollen + Bloom subsystem and endpoints for event-driven workflows
  ([`7d9e386`](https://github.com/gadz82/orchid-api/commit/7d9e3860c30f7e7fed8fcdd44ec37538441dc0ac))

### Refactoring

- **docs**: Remove phased rollout references for streamlined documentation
  ([`6e8f38d`](https://github.com/gadz82/orchid-api/commit/6e8f38d8d66efaa844c4bca032f040a2226d442d))

- **events**: Remove `HTTPIngestionProducer` from library and relocate to `orchid-api`
  ([`15bc739`](https://github.com/gadz82/orchid-api/commit/15bc739b7048bd81855d2617afc66c2677b8d612))


## v1.5.0 (2026-05-05)

### Chores

- Bump `orchid-ai` dependency to >=1.6.0 in `orchid-cli` and `orchid-api`
  ([`83fd76d`](https://github.com/gadz82/orchid-api/commit/83fd76df4e1e1a00a3c5369d3f0e7a7c86a2a63d))

### Documentation

- Update Orchid API and MCP gateway README
  ([`c7cd503`](https://github.com/gadz82/orchid-api/commit/c7cd503198b6b1d5f4f67cfa92fa37af11a73606))

### Features

- Integrate RecursiveIngestion for chunk processing in document ingestion tasks
  ([`8c45ae3`](https://github.com/gadz82/orchid-api/commit/8c45ae37a4151802efa659295f528647fa4a0e4f))


## v1.4.0 (2026-05-04)

### Features

- Add SSE handling for mini-agent lifecycle events and token stream suppression
  ([`46e61eb`](https://github.com/gadz82/orchid-api/commit/46e61eb7f86dad58f6d11203a9af86560b3013b6))

- Bump orchid-ai dependency to >=1.5.0 in API
  ([`c28e710`](https://github.com/gadz82/orchid-api/commit/c28e7105c8b3827000762eef35e9133cf1fa3195))


## v1.3.0 (2026-04-29)

### Bug Fixes

- Update documentation, tests, agent files and configs.
  ([`0a18dc9`](https://github.com/gadz82/orchid-api/commit/0a18dc9f5ece2dea3543c1834b532d4ec86faf3c))

### Features

- Add identity bridging and support for advanced OAuth flows
  ([`9bbbced`](https://github.com/gadz82/orchid-api/commit/9bbbcedbebb94c8113a7efaf4e4c8b7b39cddd3a))

- Add multi-tenant domain support to auth-info and auth-exchange routes
  ([`95447ab`](https://github.com/gadz82/orchid-api/commit/95447ab14936961f6f50a94ca04d3b0c912511b4))

- Add performance logging for streaming and message routes with metrics and detailed timing
  ([`4a0e095`](https://github.com/gadz82/orchid-api/commit/4a0e095bc71f114ec387993815af327ef3154f2d))

- Add proactive MCP capability cache warming and related session endpoints
  ([`a0108f6`](https://github.com/gadz82/orchid-api/commit/a0108f6c4831b8e1274fab2fcf6b6ed07785adbe))

- Bump orchid-ai dependency to >=1.4.0 in CLI and API
  ([`e84c8af`](https://github.com/gadz82/orchid-api/commit/e84c8af87b31b1f7c8cb7a22b6ded52aabdcb8e7))

- Mcp dedicated routes and auth support, fixing auth consistency issues
  ([`5371949`](https://github.com/gadz82/orchid-api/commit/5371949c160e5b53733c6a242093a120278028c3))

- Mcp discovery add auth config
  ([`3c4a8f2`](https://github.com/gadz82/orchid-api/commit/3c4a8f213e9d7830b953303913fa64ee9cc78faf))

- Mcp oauth discovery moved to api
  ([`56fcf6e`](https://github.com/gadz82/orchid-api/commit/56fcf6e8b99bdc9421413a4130b1ffd3ae5d2327))

- Mcp oauth management in orchid db
  ([`a65ff92`](https://github.com/gadz82/orchid-api/commit/a65ff924b02838b4656be57bfc5c9b8149fde1d0))

### Refactoring

- Remove legacy routes and tests for deprecated endpoints
  ([`8861517`](https://github.com/gadz82/orchid-api/commit/88615176928843431dc0cd67fbed1c2741bea6ce))


## v1.2.3 (2026-04-22)

### Bug Fixes

- Mcp oauth discovery, implementation of 2025-03-26 spec flow compliance.
  ([`0f9fbde`](https://github.com/gadz82/orchid-api/commit/0f9fbde1287f02f46eaf7e8c83cba552962e43ca))


## v1.2.2 (2026-04-22)

### Bug Fixes

- Mcp oauth management fixes to support http mcp oauth configuration.
  ([`1f9150c`](https://github.com/gadz82/orchid-api/commit/1f9150c902bfe8a210927a7c1bffb217a6c0ad20))

- Mcp oauth management fixes to support http mcp oauth configuration.
  ([`cfc1672`](https://github.com/gadz82/orchid-api/commit/cfc167277515a59cc43461a395c3978c282a52d7))


## v1.2.1 (2026-04-21)

### Bug Fixes

- Missing mcp_token_store property accessor
  ([`6ee7509`](https://github.com/gadz82/orchid-api/commit/6ee7509b43d18d44e237a3c239b0a04464b29143))


## v1.2.0 (2026-04-21)

### Bug Fixes

- Bump orchid-ai dependency to >=1.3.2 in CLI and API
  ([`2944dcf`](https://github.com/gadz82/orchid-api/commit/2944dcf22224ebb6a286f81faa242477acfea585))

### Features

- **api**: Add support for integrator-supplied migration packages
  ([`500a899`](https://github.com/gadz82/orchid-api/commit/500a899d1fdbff0f1d37575e71d36f81743594bb))


## v1.1.1 (2026-04-17)

### Bug Fixes

- **streaming**: Fix fallback for supervisor's direct final responses.
  ([`da18e35`](https://github.com/gadz82/orchid-api/commit/da18e35f7c3ec93b1b94eb1f5b35975379e86999))


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
