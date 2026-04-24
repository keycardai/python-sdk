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
