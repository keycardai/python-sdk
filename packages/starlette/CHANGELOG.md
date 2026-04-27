## 0.3.0-keycardai-starlette (2026-04-27)


- feat(keycardai-starlette): emit DeprecationWarning from BearerAuthMiddleware and verify_bearer_token (#99)
- Closes ACC-234. PR #97 retained the legacy bearer surface as docstring-only deprecated shims so keycardai-mcp and keycardai-agents keep working until they migrate (ACC-235, ACC-229..232). Without a runtime signal, non-MCP downstream users importing these symbols get no notice before the symbols disappear.
- Changes:
- BearerAuthMiddleware.__init__ emits DeprecationWarning pointing at AuthenticationMiddleware + KeycardAuthBackend
- verify_bearer_token emits DeprecationWarning pointing at KeycardAuthBackend
- BearerAuthMiddleware.dispatch passes _from_middleware=True so a single middleware instantiation fires exactly one warning total, not one per request
- New tests: warning fires on init, warning fires on direct verify_bearer_token call, dispatch path does not double-warn
- _create_auth_challenge_response is intentionally not warned: it is underscored, not in __all__, and not re-exported by the keycardai-mcp shims, so no external caller can plausibly hit it directly.
- Verified mcp tests still pass (560/560). Agents tests fail on a pre-existing a2a-sdk import error unrelated to this change.

## 0.2.0-keycardai-starlette (2026-04-26)


- feat(keycardai-starlette): new package for Starlette/FastAPI Keycard integration (#97)
- * feat(keycardai-starlette-oauth): new package for Starlette/FastAPI OAuth middleware
- Implements Tier 2 of the Protocol-Agnostic SDK KEP: a new
keycardai-starlette-oauth package that provides Starlette-specific
middleware and route builders without any MCP dependency.
- New package (packages/starlette-oauth/):
- middleware/bearer.py: BearerAuthMiddleware
- handlers/metadata.py: RFC 9728 + RFC 8414 metadata with local
  ProtectedResourceMetadata model (no mcp.shared.auth dependency)
- handlers/jwks.py: JWKS endpoint handler
- routers/metadata.py: Route builders + protected_router()
- provider.py: AuthProvider with install() and @protect() decorator
- shared/starlette.py: Proxy-aware URL helpers
- keycardai-mcp changes:
- Now depends on keycardai-starlette-oauth (starlette removed from
  direct deps since it comes transitively)
- Server middleware/handlers/routers replaced with re-export shims
- protected_mcp_router wraps protected_router with mcp_app kwarg compat
- All existing imports continue to work
- * refactor(keycardai-starlette): rename from keycardai-starlette-oauth
- Per revised KEP naming decisions: drop the OAuth suffix from the
customer-facing package since it will cover more than just OAuth
(token exchange, policy enforcement, vaulted creds, etc.). The
keycardai-oauth package stays as an internal building block.
- Renames:
- packages/starlette-oauth/ → packages/starlette/
- src/keycardai/starlette_oauth/ → src/keycardai/starlette/
- keycardai-starlette-oauth → keycardai-starlette (PyPI name)
- keycardai.starlette_oauth → keycardai.starlette (import path)
- Updated workspace source, MCP dependency, and all MCP shim imports.
Backward-compat shims in keycardai-mcp continue to work.
- * feat(keycardai-starlette): add smoke tests and fix .well-known middleware bypass
- - Add 22 smoke tests covering metadata routes, AuthProvider install/config,
  and a guarantee that keycardai.starlette has no keycardai.mcp imports.
- Fix BearerAuthMiddleware to skip /.well-known/* paths. Without this,
  AuthProvider.install() (which adds the middleware globally) blocked the
  OAuth discovery endpoints it had just registered — clients got 401 trying
  to learn how to authenticate. Metadata discovery per RFC 9728 §2 must
  remain publicly reachable.
- Add fastapi and httpx to the starlette package test extras.
- Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- * chore: adjust coverage thresholds after starlette extraction
- - Add keycardai-starlette to test-coverage and test recipes
- Lower mcp threshold from 65% to 60%: the well-tested server auth code
  moved to keycardai-oauth / keycardai-starlette, leaving a higher
  proportion of under-tested client integrations (CrewAI/LangChain/OpenAI
  adapters at 14-25%) in the denominator. Absolute coverage of the
  remaining code is unchanged; the ratio is what shifted.
- Set starlette threshold to 55% (smoke tests cover the surface area;
  provider.py @protect() decorator and async client init are the main
  gap, tracked as a follow-up)
- Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- * fix(scripts): pass --yes to cz bump in version_preview for new packages
- Commitizen prompts "Is this the first tag created?" when it cannot find an
existing tag matching a package's tag_format. For brand-new packages like
keycardai-starlette that have no tag yet, this prompt EOFs in non-TTY CI
runs and causes release-preview to report an error instead of a version
delta.
- --yes auto-confirms the prompt. Existing packages with prior tags never
see the prompt, so their output is unchanged.
- Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- * chore: regenerate uv.lock with uv >= 0.9 format
- Older lock file (generated with uv 0.8.x) failed to parse on CI's newer
uv with "Dependency `pytokens` has missing `source` field but has more
than one matching package". The lock format tightened in 0.9+ to require
explicit source annotations when multiple resolution markers are in play.
- Regenerated with uv 0.11.7. Resolution now succeeds under setup-uv@v4
(unpinned, tracks latest). All package test suites still pass
(oauth 208, starlette 22, mcp 560, mcp-fastmcp 51).
- Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- * ci: wire keycardai-starlette into release workflow tag filter
- The release workflow only triggers on tag patterns explicitly listed in
on.push.tags. Without adding *-keycardai-starlette, tags created by
commitizen for the new package (e.g. 0.1.0-keycardai-starlette) would
not trigger the release job, so nothing would publish to PyPI even if a
Trusted Publisher were configured.
- Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- * chore: minimize uv.lock diff to just the keycardai-starlette addition
- The previous regeneration pass rebuilt the lock wholesale and produced
a 5-marker resolution format (splitting python_full_version >= '3.14'
into '3.15' and '3.14.*'). CI's uv 0.11.7 could not parse that,
failing with "pytokens has missing source field but has more than one
matching package" during uv sync --all-extras.
- Revert to origin/main's lock and re-run `uv lock --no-upgrade`, which
adds only the keycardai-starlette workspace member (34-line diff) and
leaves the resolution-markers block identical to main. CI parses it
cleanly; all package test suites pass.
- Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- * style: trim verbose comments added during review
- Condense the justfile coverage-threshold note and version_preview.py
--yes flag comment to one sentence each.
- Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- * fix(keycardai-starlette): address PR review feedback from cmars
- Seven correctness and style fixes:
- 1. bearer.py: tighten the auth-bypass path match. The previous
   `path.startswith("/.well-known/")` exempted ALL well-known URIs (e.g.
   `/.well-known/change-password`, `assetlinks.json`) from bearer auth.
   Replace with an explicit allowlist of OAuth metadata endpoints
   (`oauth-protected-resource`, `oauth-authorization-server`, `jwks.json`),
   matched as exact paths or delimited subpaths. Cite RFC 9728 §2 / RFC
   8414 §3 as the spec basis.
- 2. provider.py `_get_or_create_client`: the parameter was annotated
   `dict[str, str] | None = None` but every line dereferenced it
   unguarded. Drop the Optional from the signature; callers always pass
   a non-None dict.
- 3. provider.py `__init__`: construct `_init_lock = asyncio.Lock()`
   eagerly instead of lazily. The previous `if self._init_lock is None:
   self._init_lock = asyncio.Lock()` was technically safe in pure
   asyncio (no await between check and assign) but reads as a race
   smell. Eager init removes the question. asyncio.Lock can be created
   outside an event loop in Python 3.10+.
- 4. provider.py docstring: rephrase the AuthProvider class docstring to
   describe what the class does instead of what it lacks ("without any
   MCP dependency").
- 5. handlers/metadata.py `protected_resource_metadata`: return
   `JSONResponse(content=dict)` instead of `Response(content=json_string)`.
   The previous implementation served `Content-Type: text/plain`.
- 6. handlers/metadata.py `authorization_server_metadata`: pass an explicit
   `timeout=httpx.Timeout(5.0)` to `httpx.Client` so a slow upstream
   cannot pin a Starlette threadpool worker indefinitely. Switch the
   error responses to JSONResponse for the same Content-Type reason.
- 7. shared/starlette.py `get_base_url`: guard against `None` port. When
   `request_base_url.port` is None (proxy stripped it, missing from
   pydantic parsing), the previous code interpolated `:None` into the
   URL string. Now treat None like the default ports (omit).
- Adds regression tests:
- `/.well-known/change-password` returns 401 (path-specific bypass)
- `/.well-known/oauth-protected-resource/zone-id/path` returns 200
- `_init_lock` is an asyncio.Lock after `__init__`
- `Content-Type` is `application/json` on the metadata response
- `httpx.Client` is constructed with an explicit `timeout=` kwarg
- Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- * refactor(keycardai-starlette): make install() per-route opt-in instead of whole-app lockdown
- The previous install() shape added BearerAuthMiddleware globally so every
route in the FastAPI/Starlette app required a bearer token. A /health or
/version endpoint returned 401, which contradicts the framing in the
Protect Any API guide ("an API that knows which agent is calling") and the
existing per-subtree code patterns the docs already show
(BearerAuthMiddleware on a Mount, protected_mcp_router(...)).
- After this change:
- install(app) adds OAuth metadata routes only (.well-known/oauth-*).
  No global middleware. Routes are public by default.
- @auth.protect() (no args) verifies the bearer token, returns 401 on
  missing/invalid. No delegation, no AccessContext required.
- @auth.protect("resource") verifies + runs delegated token exchange and
  populates an AccessContext as before.
- protected_router() is unchanged. Still the right pattern for protecting
  a whole subtree (MCP transport, internal admin app, etc.).
- Implementation:
- Extract the verification body of BearerAuthMiddleware.dispatch() into a
  free verify_bearer_token(request, verifier) helper that returns either an
  auth_info dict on success or an RFC 6750 challenge Response on failure.
  Both the middleware and the decorator call it.
- The decorator reuses request.state.keycardai_auth_info if the middleware
  already populated it (e.g. inside a protected_router() mount), otherwise
  calls verify_bearer_token itself and returns the 401 directly on failure.
- AccessContext lookup and injection only run when resources is set.
- Test changes:
- Removed test_install_rejects_requests_without_bearer_token (old contract).
- Removed test_install_does_not_bypass_unrelated_well_known_paths (without
  global middleware, /.well-known/change-password is now a 404, which the
  framework provides; nothing for us to assert here).
- Added test_install_does_not_block_unprotected_routes: /health stays 200.
- Added test_install_does_not_add_global_middleware: BearerAuthMiddleware
  is NOT in app.user_middleware after install().
- Added TestProtectDecorator class:
  - no-args form returns 401 without bearer
  - resource form returns 401 without bearer
  - no-args form does not require AccessContext on the function signature
  - decorator reuses request.state when middleware preset it (verify_token
    asserts if called)
- README and module docstrings rewritten to show the new model with three
distinct patterns (decorator no-args, decorator with resource, protected_router).
- Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- * style(keycardai-starlette): drop temporal/historical comments and tighten test names
- The previous refactor commit shipped a few comments framed against the
prior code shape ("Reuse middleware-set auth info if BearerAuthMiddleware
ran ... otherwise verify the bearer token here") and a couple of
section-header style comments restating what the code does. Drop them.
Move the "two-call-sites" framing out of the verify_bearer_token
docstring; describe the present contract.
- Rename test_install_does_not_add_global_middleware to
test_install_leaves_user_middleware_stack_empty and
test_install_does_not_block_unprotected_routes to
test_routes_without_protect_decorator_stay_public for clearer positive
framing.
- Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- * Kamil/starlette auth model (#98)
- * align keycardai-starlette with starlette authentication framework
- * add protected_resource_server example for keycardai-starlette
- * prevent transitive load_dotenv from polluting mcp test environment
- * fix(lint): resolve ruff B026 and I001 errors after merging #98
- Three errors flagged by `just check` after the #98 merge:
- - packages/mcp/tests/conftest.py: B026 star-arg unpacking after keyword
  argument. Forward dotenv_path/stream positionally to the real load_dotenv.
- packages/starlette/src/keycardai/starlette/authorization.py: I001 import
  ordering (auto-fixed).
- packages/starlette/src/keycardai/starlette/provider.py: I001 import
  ordering (auto-fixed).
- All test suites still pass: starlette 42, mcp 560, oauth 208, mcp-fastmcp 51.
- Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- * refactor(keycardai-starlette): tighten review findings before merge
- - mcp.server.routers re-exports the protected_mcp_router wrapper so the
  mcp_app= kwarg keeps working through the package-level import
- consolidate the RFC 6750 challenge response into one helper shared by
  keycard_on_error and the @requires/@auth.grant decorators
- drop KeycardUser.resource_client_id (was always equal to
  resource_server_url); grant.wrapper reads resource_server_url for both
  auth_info dict keys
- type _get_or_create_client auth_info as dict[str, str | None] so
  zone_id is no longer mistyped as str
- replace test that asserted staticmethod identity with regression tests
  for the well-known bypass: OAuth metadata paths short-circuit, sibling
  paths (change-password, security.txt, oauth-protected-resource-fake,
  openid-configuration) still raise KeycardAuthError
- rewrite test_no_auth_header_returns_none to call the backend directly
  instead of building a FastAPI app and patching middleware kwargs
- ---------
- Co-authored-by: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
Co-authored-by: Kamil <kamil@keycard.ai>
