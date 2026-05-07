## 0.12.0-keycardai-oauth (2026-05-07)


- feat(keycardai-oauth)!: pass error context through AccessContext.access(), rename to get_resource_error (#114)
- * feat(keycardai-oauth)!: pass error context through AccessContext.access(), rename to get_resource_error
---------
- Co-authored-by: GitHub Action <action@github.com>

## 0.11.0-keycardai-oauth (2026-04-28)


- feat(keycardai-oauth): add high-level PKCE user-login flow (ACC-229) (#101)
- * feat(keycardai-oauth): add high-level PKCE flow client (ACC-229)
- First step of the keycardai-agents decomposition (ACC-229..232). Per the
revised KEP, the OAuth PKCE user-login flow is generic OAuth code with no
agents-specific concerns and belongs in keycardai-oauth next to the rest
of the OAuth client primitives.
- New module keycardai.oauth.pkce:
- - PKCEClient orchestrates the full authorization-code-with-PKCE flow:
  parse the WWW-Authenticate challenge (RFC 9728), fetch protected resource
  and authorization server metadata (RFC 8414), open the browser at the
  authorize endpoint, capture the redirect via a local callback server,
  and exchange the code at the token endpoint. Returns the token endpoint
  response dict directly.
- OAuthCallbackServer is the loopback redirect catcher (RFC 8252) used by
  PKCEClient; exported separately so callers running their own flow on top
  of the lower-level PKCEGenerator + build_authorize_url primitives can
  reuse the callback machinery.
- 7 new tests cover header parsing, discovery error paths, the happy-path
  flow, and confidential vs public client auth on the token endpoint.
- keycardai-agents changes:
- - AgentClient now composes PKCEClient instead of carrying its own copy of
  the auth flow. AgentClient.authenticate(...) is preserved as a thin shim
  that returns the access_token string and updates the per-service token
  cache, so existing /invoke retry-on-401 behavior is unchanged.
- AgentClient drops ~370 lines of duplicated PKCE/discovery/callback code.
- keycardai.agents.client.oauth re-exports OAuthCallbackServer through a
  module __getattr__ that emits a DeprecationWarning pointing at the new
  canonical import path.
- Stale tests in test_agent_client_oauth.py that exercised AgentClient
  private methods (_extract_resource_metadata_url, _fetch_resource_metadata,
  _fetch_authorization_server_metadata) removed; equivalent contracts now
  live in the keycardai-oauth PKCE test suite.
- Verified: oauth 215/215 (was 208 + 7 new), agents 81/81 (was 85 - 4 removed
implementation tests), mcp 560/560, starlette 49/49, ruff clean.
- Stacked on #100 (ACC-236 a2a-sdk pin) so the agents test suite can run
during validation.
- * fix(keycardai-oauth): address review findings on PKCE move
- Three small fixes from the review of #101:
- 1. PKCEClient now accepts an optional injected httpx.AsyncClient. AgentClient
   passes its existing http_client through, so a single connection pool covers
   both the agent service calls and the OAuth flow. close() only closes the
   client it owns. Restores the one-pool-per-AgentClient behavior from main.
- 2. Drop the no-op rstrip("/") + "/" round-trip in PKCEClient.authenticate
   when building the authorization server discovery URL.
- 3. Assert the discovery URL path in test_authenticate_completes_full_flow.
   The previous test stubbed http_mock.get with side_effects but never
   verified what URLs were passed; a typo from oauth-authorization-server
   to openid-configuration would have gone unnoticed.
- * refactor(keycardai-oauth): collapse PKCEClient into a flow function on AsyncClient
- Per Kamil review feedback (#101): a separate PKCEClient sitting next to
AsyncClient invited "which client do I use?" The OAuth-server-facing
operations belong on the existing AsyncClient.
- Changes:
- - keycardai.oauth.pkce.PKCEClient (class) -> keycardai.oauth.pkce.authenticate
  (module-level async function). One-shot per user login, no state worth
  preserving across calls.
- The function uses AsyncClient internally for server metadata discovery
  (RFC 8414) and code exchange. AsyncClient is now the only thing in
  keycardai.oauth that talks to OAuth servers as a client.
- AsyncClient.exchange_authorization_code (and Client + the underlying
  operations._authorize helpers) gain an optional resource= parameter so
  RFC 8707 tokens still work through the canonical path.
- The pkce module retains the user-flow concerns: RFC 9728 challenge
  parsing, resource metadata fetch (paired with the protected resource,
  not the OAuth server), browser launch, and the loopback callback server
  (RFC 8252).
- AgentClient drops the cached _pkce instance and just calls the function
  per /invoke retry, passing its own httpx.AsyncClient through for the
  resource metadata fetch.
- Tests rewritten for the function shape: 7/7 passing, same coverage
  (header parsing, discovery error paths, happy path with resource
  indicator, public vs confidential auth on the token endpoint).
- Verified: oauth 215/215, agents 81/81, mcp 560/560, starlette 49/49.
ruff clean.

## 0.10.0-keycardai-oauth (2026-04-24)


- fix(keycardai-oauth): fall back to legacy ./mcp_keys dir with deprecation warning
- Switch WebIdentity default storage_dir back to ./server_keys (aligning
with the protocol-agnostic naming from this PR), but transparently fall
back to ./mcp_keys when no storage_dir is passed, ./server_keys does not
exist, and ./mcp_keys does. The fallback emits a DeprecationWarning
pointing at the explicit configuration or migration paths.
- This preserves zero-config upgrades for existing keycardai-mcp services
(they keep finding their existing keys) while giving new installs the
new default. The fallback will be removed in a future release.
- Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- fix(keycardai-oauth): preserve mcp storage defaults, move server tests
- Address PR #95 review comments from cmars:
- 1. Revert WebIdentity default storage_dir to "./mcp_keys" and key_id
   prefix to "mcp-server-". Changing these would silently break existing
   keycardai-mcp services on upgrade: they would look for keys in a new
   empty directory and regenerate identity, losing their registered client
   identity with Keycard.
- 2. Move oauth-server-specific tests (test_verifier, test_cache,
   test_application_identity -> test_credentials) from packages/mcp/tests
   to packages/oauth/tests/keycardai/oauth/server/ so coverage lives
   with the canonical oauth.server modules.
- Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- fix(keycardai-oauth): address PR review findings
- - Add token_exchange module with exchange_tokens_for_resources()
  orchestration (KEP Tier 1 gap)
- Rename WebIdentity param mcp_server_name -> server_name with
  backward-compatible alias; default storage dir ./mcp_keys -> ./server_keys
- Add mcp_server_url/missing_mcp_server_url backward-compat aliases
  to AuthProviderConfigurationError (prevents breaking fastmcp callers)
- Fix _get_kid_and_algorithm returning list instead of tuple
- feat(keycardai-oauth): add server subpackage with framework-free primitives
- Extract protocol-agnostic server components from keycardai-mcp into
keycardai.oauth.server per the Protocol-Agnostic SDK KEP (Tier 1).
- New keycardai.oauth.server modules:
- access_context: AccessContext for non-throwing token access
- credentials: ApplicationCredential, ClientSecret, WebIdentity, EKSWorkloadIdentity
- verifier: TokenVerifier with local AccessToken model (no MCP dependency)
- exceptions: OAuthServerError base + all framework-free exceptions
- _cache: JWKSCache/JWKSKey for JWKS key caching
- client_factory: ClientFactory protocol + DefaultClientFactory
- private_key: PrivateKeyManager, FilePrivateKeyStorage
- keycardai-mcp changes:
- Server auth modules now re-export from keycardai.oauth.server
- MCPServerError is an alias for OAuthServerError
- MissingContextError stays MCP-specific (references FastMCP Context)
- All existing imports continue to work (no breaking changes)
- Tests updated to patch canonical module paths

## 0.9.0-keycardai-oauth (2026-04-02)


- feat(keycardai-oauth): support for impersonation token exchange
- - Add substitute-user token type and unsigned JWT builder
- Add impersonate method to Client and AsyncClient
- Add user_identifier callback to MCP grant decorator
- Add impersonation token exchange example

## 0.8.0-keycardai-oauth (2026-04-02)


- feat(keycardai-oauth): add authorization code exchange and PKCE support
- - Implement PKCE code verifier, challenge generation, and validation
- Add authorization code exchange operation (sync and async)
- Add build_authorize_url for constructing OAuth authorize URLs
- Add exchange_authorization_code to Client and AsyncClient
- Add get_endpoints/endpoints property to expose resolved endpoints
- Add id_token field to TokenResponse

## 0.7.0-keycardai-oauth (2026-03-06)


- fix(keycardai-oauth): update test to expect OAuthProtocolError for structured error bodies
- feat(keycardai-oauth)!: detailed error reporting
- BREAKING CHANGE: Token exchange HTTP 4xx errors with structured JSON bodies now raise OAuthProtocolError instead of OAuthHttpError. Callers catching OAuthHttpError for these responses must update to catch OAuthProtocolError.

## 0.6.0-keycardai-oauth (2025-11-17)


- feat(keycardai-oauth): client metadata updates

## 0.5.0-keycardai-oauth (2025-09-22)


- feat(keycardai-oauth): client assertion support
- feat(keycardai-oauth): JWKS type support

## 0.4.1-keycardai-oauth (2025-09-17)


- fix(keycardai-oauth): audience checks

## 0.4.0-keycardai-oauth (2025-09-16)


- feat(keycardai-oauth): multi-zone authentication strategy

## 0.3.0-keycardai-oauth (2025-09-16)


- feat(keycardai-oauth): jwt capabilities

## 0.2.0-keycardai-oauth (2025-09-10)


- feat(keycardai-oauth): remove the impersonation logic

## 0.1.0-keycardai-oauth (2025-09-07)


- feat(keycardai-oauth): initial release
