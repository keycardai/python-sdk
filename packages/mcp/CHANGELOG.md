## 2.0.0-keycardai-mcp (2026-08-07)


- feat(keycardai-mcp)!: port to mcp 2.0 (ECO-195) (#218)
- * feat(keycardai-mcp)!: port to mcp 2.0
- mcp 2.0 removed the bundled FastMCP 1.x (mcp.server.fastmcp). Its
successor in-package is mcp.server.mcpserver.MCPServer, which exposes
the same two APIs AuthProvider.app() uses (session_manager and
streamable_http_app), so the retarget is direct.
- Import moves:
  mcp.server.fastmcp.{Context,FastMCP} -> mcp.server.mcpserver.{Context,MCPServer}
  mcp.shared.context.RequestContext    -> mcp.server.context.ServerRequestContext
  streamablehttp_client                -> streamable_http_client
- mcp 2.0 also renamed model fields to snake_case. Constructors still
accept the camelCase aliases, so only attribute reads broke:
result.nextCursor and mcp_tool.inputSchema. The agent integrations
guarded theirs with hasattr(), which under 2.0 returns False and
silently yields an empty tool schema rather than raising, so those are
corrected too.
- Breaking: AuthProvider.app() is typed MCPServer instead of FastMCP.
Consumers needing mcp 1.x pin the prior keycardai-mcp minor.
- keycardai-fastmcp is untouched and unaffected. It only imports
credential types, ClientFactory and exceptions from this package, and
it stays on fastmcp 3.x / mcp 1.x until fastmcp 4.0 is stable.
- Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
- * build: split the fastmcp packages out of the uv workspace for mcp 2.0
- packages/mcp now requires mcp>=2.0. fastmcp 3.x, openai-agents and crewai
all still pin mcp<2.0, and a uv workspace resolves to a single lock, so
they cannot co-resolve.
- Workspace: packages/fastmcp and packages/mcp-fastmcp move to
workspace.exclude and carry their own resolution. They pick up
keycardai-mcp from the index (0.26.0, mcp 1.x) rather than the local
path, which is the honest arrangement while the two sit on different
mcp majors.
- packages/mcp test extra: drops fastmcp, keycardai-mcp-fastmcp,
openai-agents and crewai. The suites that need them are guarded by
pytest.importorskip and report SKIPPED, so the gap shows in test output
instead of disappearing. test_agent_integrations.py gained a guard; it
was importing agents at module scope and failing collection.
- Release tooling: scripts/changelog.py enumerated packages from
workspace.members, so both excluded packages would have silently dropped
out of version bumps and changelogs. Added tool.keycardai.release
standalone-members, which it now unions in. Verified all 7 packages are
still discovered.
- packages/fastmcp gains real CI coverage for the first time (ECO-172): it
is added to the justfile test and coverage targets, and its test extra
was missing pytest-cov and requests, which it had been getting
incidentally from the shared workspace environment.
- Coverage gates all pass: oauth 84.38%, starlette 79.57%, mcp 62.60%,
fastmcp 85.01%, mcp-fastmcp 100%, a2a 45 tests.
- Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
- * build: drop the mcp<2.0-pinned agent frameworks from packages/mcp
- Socket blocked the previous commit, correctly. Removing openai-agents
and crewai was not enough: pydantic-ai also pins mcp<2.0, transitively
via fastmcp-slim[client]<4. Left in place, the resolver backtracked
pydantic-ai 2.x -> 1.44.0 to satisfy mcp>=2.0, and 1.44.0 pulled
fastmcp 2.14.1, which carries the known fastmcp advisory (Socket
vulnerability score 25).
- Removed from packages/mcp:
  - test extra: pydantic-ai (alongside openai-agents and crewai)
  - optional-dependencies: the crewai and pydantic-ai convenience extras
- The extras go because pip install keycardai-mcp[crewai] cannot resolve
against mcp>=2.0 regardless. The integration modules still ship; bring
your own framework install on a pre-2.0 keycardai-mcp.
- Root lock no longer contains fastmcp, pydantic-ai, crewai or
openai-agents at any version, and resolves mcp 2.0.0.
- Coverage note, so the number is not misread: packages/mcp reports 75.10%
here against 62.60% before. That is not an improvement. The four
*_agents.py integration modules are no longer imported, so coverage.py
drops them from the report and the denominator shrinks from ~2500 to
2036 statements. The same code is untested; less of it is now measured.
- Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
- * fix: address review on the mcp 2.0 split
- Release blocker. release.yml ran `uv build --package <name>`, which
resolves against workspace.members -- exactly what the split removed the
two fastmcp packages from. Verified: `uv build --package
keycardai-fastmcp` errors with "not found in workspace", same for
keycardai-mcp-fastmcp. changelog.py being correct made this worse, not
better: tags get cut and changelogs written, then publish dies. Now
builds by path using the package-dir output detect-package already
emits. Verified for both excluded packages and two workspace members.
- Resolution trap. packages/fastmcp declared keycardai-mcp>=0.15.0
unbounded alongside fastmcp>=3.1.0 (mcp<2.0). Since keycardai-mcp 0.27+
needs mcp>=2.0, the pair is unsatisfiable, so pip silently backtracked
keycardai-mcp to 0.26.0 and froze there. Capped <0.27 so the conflict is
explicit.
- mcp>=2.0.0 was unbounded, reproducing this exact incident on mcp 3.0.
Capped <3.0.
- Sibling isolation. The standalone locks took every keycardai-* package
from the index, so a breaking change in packages/oauth no longer failed
packages/fastmcp. keycardai-oauth is now path-linked in both, and
keycardai-fastmcp is path-linked into the bridge (same mcp<2.0 major).
Only keycardai-mcp stays on the index, where the major genuinely
differs. keycardai-starlette also stays: it is transitive via
keycardai-mcp and tool.uv.sources only redirects direct dependencies.
- The input_schema fix was unverified. The existing pydantic test sets
inputSchema on a MagicMock, where hasattr is unconditionally True, so it
cannot tell the two spellings apart. Added
test_tool_schema_reads.py, which drives the real
_convert_mcp_tool_to_langchain with a real mcp.types.Tool and asserts
the generated args_schema carries the field. Confirmed it fails when the
camelCase read is restored.
- tests/conftest.py imported dotenv unguarded. It is a crewai-only shim
and crewai is no longer installable here, so the single-package flow
errored at collection. Guarded.
- Coverage gate stays 60, but the denominator it measures grew to 2679
statements against ~2500 before, because the new test imports the
integration modules and pulls all four back into measurement. Removing
the frameworks alone had shrunk it to 2036 and inflated the figure to
~75% on less code. The reviewer's suggested 72 was correct for that
2036 state and no longer applies. Headroom is thin at 60.88%.
- Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
- * fix(keycardai-fastmcp): target the 0.27.x line, not below it
- Rebased onto main now that #219 shipped the mcp<2.0 cap as keycardai-mcp
0.27.0.
- That version number invalidates the cap this branch carried.
packages/fastmcp pinned "keycardai-mcp>=0.15.0,<0.27" on the assumption
that 0.27 would be the mcp 2.0 port. It is not: 0.27.0 is the cap
release, and it is the only published version that constrains mcp
correctly.
-   keycardai-mcp 0.26.0  mcp>=1.13.1        unbounded -> resolves mcp 2.0
  keycardai-mcp 0.27.0  mcp>=1.28.1,<2.0   safe
- So "<0.27" excluded the one good version and forced the broken one.
Now ">=0.27,<0.28": the floor guarantees the mcp cap is present, the
ceiling excludes the 2.0 line this branch starts. Both standalone locks
verified resolving keycardai-mcp 0.27.0 with mcp 1.29.0.
- Also resolved the pyproject conflict against main's cap by keeping this
branch's mcp>=2.0.0,<3.0, and regenerated all three lockfiles from the
merged manifests rather than hand-merging them.
- Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
- * fix(keycardai-fastmcp): pair with the keycardai-mcp 1.x line
- keycardai-mcp 1.0.0 is on PyPI and release/mcp-v1 exists, so the
fastmcp cap moves from the interim 0.27.x window to >=1,<2. Both
standalone locks re-resolved to 1.0.0.
- Also flips packages/mcp's major_version_zero to false. The package is
past 1.0.0 and the flag caps breaking changes at minor: left true, this
PR's feat! merge would release the mcp 2.0 port as keycardai-mcp 1.1.0,
which is exactly what happened in typescript-sdk's cutover before the
same fix there.
- Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- ---------
- Co-authored-by: GitHub Action <action@github.com>
Co-authored-by: Claude Opus 5 (1M context) <noreply@anthropic.com>

## 1.0.0-keycardai-mcp (2026-08-06)

## 0.27.1-keycardai-mcp (2026-07-30)


- fix(keycardai-mcp): synchronous OAuth completion cleanup, no stale auth challenges (#205)
- * fix(keycardai-mcp): run OAuth completion cleanup synchronously before returning
- Completion cleanup (PKCE state delete, pending-auth clear, and the
completion-route delete in CompletionRouter) previously ran via bare
asyncio.create_task on the background path. The event loop keeps only a
weak reference to such tasks, so they could be garbage-collected before
running (or cancelled at shutdown with the CancelledError swallowed),
leaving a stale pending-auth record that connected sessions kept
advertising as an auth challenge.
- Both cleanup steps are fast storage calls, so they now always run
synchronously before the completion result is returned, for every
coordinator:
- - oauth_completion_handler awaits its cleanup unconditionally; the
  run_cleanup_in_background parameter is deprecated and ignored (a
  DeprecationWarning fires when it is passed explicitly)
- CompletionRouter.route_completion awaits the route-metadata delete
  instead of scheduling a fire-and-forget task
- a failure deleting the PKCE state no longer skips clearing the
  pending-auth record; each cleanup step is attempted independently
- CancelledError is no longer swallowed by cleanup helpers
- AuthCoordinator.requires_synchronous_cleanup is deprecated (kept for
  backward compatibility, returns True); OAuthStrategy no longer
  consults it and LocalAuthCoordinator's override is removed
- Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- * fix(keycardai-mcp): never surface an auth challenge for a connected session
- Session.get_auth_challenge() was a pure storage read, so a pending-auth
record that outlived a completed authorization (for example when cleanup
did not run) kept being advertised as an auth challenge after the session
auto-reconnected. Consumers of get_auth_challenges() had to filter it out
themselves until the record's TTL expired, and custom storage backends
that ignore ttl surfaced it indefinitely.
- A CONNECTED session has no pending challenge by definition:
get_auth_challenge() now returns None in that state and lazily clears any
stale stored record. All other statuses keep the storage read, so a
session in AUTH_PENDING still surfaces its challenge. This is defense in
depth on top of the synchronous completion cleanup; the lazy clear only
helps in-process, which is fine for a secondary guard.
- Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- * fix(keycardai-mcp): clear stale auth-pending records on connect, not in the getter
- The CONNECTED short-circuit in Session.get_auth_challenge() clobbered
fresh re-auth challenges. A mid-session 401 (token expiry on a live
tool call) is handled by the httpx transport, which writes a new
pending-auth record without ever changing the session status, so a
long-lived CONNECTED session with an expired token had its fresh
challenge deleted by the getter and get_auth_challenges() returned [],
hiding the re-auth URL from the user. A destructive write inside a
getter that polling APIs hit repeatedly was also the wrong shape.
- get_auth_challenge() is a plain storage read again. The stale-record
clear moves to the transition into CONNECTED in _initialize_session:
reaching CONNECTED means auth works, so any record stored at that
point is stale by definition. The clear runs once at the transition,
is best-effort (storage errors never fail the connection), and cannot
clobber a mid-session re-auth challenge because that is written while
the session is already CONNECTED. The auto-reconnect path in
on_completion_handled flows through connect() into
_initialize_session, so one hook covers both paths.
- Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- * fix(keycardai-mcp): bound synchronous OAuth cleanup with a timeout
- Completion cleanup runs synchronously on the OAuth callback path, so a
hung storage delete (remote backends such as Redis or DynamoDB) would
stall the user-facing callback response indefinitely. Each cleanup
storage call (PKCE state delete, pending-auth clear, completion-route
delete) is now wrapped in asyncio.wait_for with a 5 second bound.
- A timeout falls through to the existing logged-and-swallowed per-step
handling, so a timed-out step never skips the remaining steps.
asyncio.wait_for raises TimeoutError (asyncio.TimeoutError on Python
3.10, an alias of the builtin on 3.11+), an Exception subclass on
every supported Python, while outer cancellation (CancelledError, a
BaseException) still propagates.
- Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- ---------
- Co-authored-by: GitHub Action <action@github.com>
Co-authored-by: Claude Fable 5 <noreply@anthropic.com>

## 0.27.0-keycardai-mcp (2026-07-29)


- fix(keycardai-mcp): cap mcp below 2.0 (#219)
- mcp 2.0.0 published 2026-07-28. The floor was unbounded, so fresh
installs began resolving onto the new major immediately, against code
written for 1.x.
- Capping rather than porting because the wider ecosystem has not crossed
over. Two of the four agent frameworks this package ships integrations
for still pin mcp below 2.0 outright:
-   openai-agents 0.19.0  mcp<2,>=1.19.0
  crewai        1.15.8  mcp~=1.28.1
- fastmcp 3.x pins it too. A hard mcp>=2.0 floor here would make
keycardai-mcp uninstallable next to any of them.
- The 2.0 port itself is written and green in #218; it lands once those
pins move.
- Co-authored-by: GitHub Action <action@github.com>
Co-authored-by: Claude Opus 5 (1M context) <noreply@anthropic.com>

## 0.26.0-keycardai-mcp (2026-06-15)


- feat(keycardai-mcp): resolve multi-zone credentials by zone issuer URL
- The provider looks up per-zone credentials with the zone-scoped issuer
URL it already constructs for the zone client.

## 0.25.0-keycardai-mcp (2026-06-03)


- fix(keycardai-mcp)!: enforce authentication on the MCP mount
- build(keycardai-mcp): require keycardai-starlette>=0.6.0 for require_authentication
- fix(keycardai-mcp): read delegated auth info from request.user

## 0.24.0-keycardai-mcp (2026-06-02)


- test(keycardai-mcp): use generic scope in grant request_scopes tests
- Replace the databricks-specific scope with a generic "read".
- feat(keycardai-mcp): add request_scopes to grant()
- Forward an optional per-resource OAuth scope into the token exchange,
including the impersonation branch (client.impersonate(scope=...)) plus
the application-credential and basic branches. Accepts a str, list[str],
or per-resource dict; distinct from inbound required_scopes.

## 0.23.0-keycardai-mcp (2026-04-28)


- refactor(keycardai-mcp): drop deprecated bearer middleware shims (ACC-235) (#104)
- Removes the keycardai.mcp.server.middleware re-export shims that pointed
at the deprecated BearerAuthMiddleware in keycardai-starlette. Anyone
importing BearerAuthMiddleware should switch to AuthenticationMiddleware
with backend=KeycardAuthBackend(verifier) and on_error=keycard_on_error.
The deprecated symbols themselves stay in keycardai-starlette and come
out in ACC-237.
- keycardai-agents repointed at keycardai.starlette.middleware.bearer so
it keeps building. It still emits the DeprecationWarning shipped in
keycardai-starlette 0.3.0; that goes away when ACC-232 archives the
package.
- Bearer-helper unit tests (_get_bearer_token, _get_oauth_protected_resource_url)
moved from packages/mcp/tests to packages/starlette/tests where the
helpers live.

## 0.22.0-keycardai-mcp (2026-04-24)


- fix(keycardai-mcp): resolve ruff lint errors in provider and test imports

## 0.21.0-keycardai-mcp (2026-03-06)


- build(keycardai-mcp): bump keycardai-oauth dependency to >=0.7.0
- refactor(keycardai-mcp)!: optimize error formatting in token exchange chain
- Restructure error dicts to remove redundancy and improve readability.
Key renames: error->message, error_code->code, error_description->description,
resource_errors->resources. Only include raw_error for non-OAuth exceptions.
- BREAKING CHANGE: Error dict keys renamed: error->message, error_code->code, error_description->description. The get_errors() output key resource_errors is now resources.

## 0.20.1-keycardai-mcp (2026-02-06)


- fix(keycardai-mcp): return prm for resources dynamically

## 0.20.0-keycardai-mcp (2026-01-07)


- feat(keycardai-mcp): Adds PydanticAI integration for MCP frameworks
- - Adds PaydanticAI adapter to client integrations directory
- Support for PydanticAI agents with secure MCP tool access
- Follows established pattern with LangChain and OpenAI integrations
- Adds tests for PydanticAI integration imports

## 0.19.0-keycardai-mcp (2026-01-07)


- feat(keycardai-mcp): Add greater control over OAuth metadata location
- - Refactors `auth_metadata_mount` into it's component parts
- Exposes mounts for individual metadata
- Allows the user to specify exactly where their OAuth metadata is
exposed
- NOTE: This is only for advanced use cases where you know you need
something non-standard. Otherwise, follow the OAuth spec.

## 0.18.0-keycardai-mcp (2025-12-04)


- feat(keycardai-mcp): add CrewAI integration for agent frameworks
- - Add CrewAI adapter to client integrations directory
- Support for CrewAI agents with secure MCP tool access
- No token passing - agents never receive raw API tokens
- Fresh token fetched per API call through Keycard
- Follows established pattern with LangChain and OpenAI integrations
- Deleted separate packages/agents package (not needed)
- Added optional dependencies: crewai and agents extras
- Added tests for CrewAI integration imports

## 0.17.0-keycardai-mcp (2025-11-18)


- feat(keycardai-mcp): session callback notification
- feat(keycardai-mcp): session lifecycle management

## 0.16.0-keycardai-mcp (2025-11-17)


- feat(keycardai-mcp): headless clients
- feat(keycardai-mcp): update oauth deps
- feat(keycardai-mcp): client implementation

## 0.15.0-keycardai-mcp (2025-11-07)


- feat(keycardai-mcp): enable web token eks env

## 0.14.0-keycardai-mcp (2025-11-06)


- feat(keycardai-mcp): configure mcp url via env

## 0.13.0-keycardai-mcp (2025-11-05)


- feat(keycardai-mcp): zone settings via env

## 0.12.0-keycardai-mcp (2025-11-05)


- feat(keycardai-mcp): automatic app cred discovery
- feat(keycardai-mcp): default eks env

## 0.11.0-keycardai-mcp (2025-10-29)


- feat(keycardai-mcp): release latest version
- Release current version of workload identity implementation

## 0.10.0-keycardai-mcp (2025-10-27)


- feat(keycardai-mcp): cach the application credentials
- feat(keycardai-mcp): app credential grant flow

## 0.9.0-keycardai-mcp (2025-10-20)


- refactor(keycardai-mcp): align credential names
- feat(keycardai-mcp): eks workload identity support
- feat(keycardai-mcp): add application authentication

## 0.8.1-keycardai-mcp (2025-10-10)


- fix(keycardai-mcp): wrong base url in auth metadata

## 0.8.0-keycardai-mcp (2025-10-07)


- refactor(keycardai-mcp): improve error messages
- refactor(keycardai-mcp): improves the error messages to provide useful debug information

## 0.7.1-keycardai-mcp (2025-09-29)


- fix(keycardai-mcp): set audience for client assertions

## 0.7.0-keycardai-mcp (2025-09-27)


- feat(keycardai-mcp): lowlevel support for RequestContext

## 0.6.0-keycardai-mcp (2025-09-23)


- feat(keycardai-mcp): enable custom middleware injection

## 0.5.1-keycardai-mcp (2025-09-22)


- fix(keycardai-mcp): support x-forwarded-port header

## 0.5.0-keycardai-mcp (2025-09-22)


- feat(keycardai-mcp): dcr can be toggled on/off
- feat(keycardai-mcp): private key jwt support with global key
- feat(keycardai-mcp): grant decorator exception handling
- feat(keycardai-mcp): private key manager protocol

## 0.4.1-keycardai-mcp (2025-09-18)


- fix(keycardai-mcp): support both sync and async tool calls

## 0.4.0-keycardai-mcp (2025-09-18)


- feat(keycardai-mcp): default domain handling

## 0.3.1-keycardai-mcp (2025-09-17)


- fix(keycardai-mcp): check audience when configured

## 0.3.0-keycardai-mcp (2025-09-16)


- feat(keycardai-mcp): multi-zone mcp routing
- feat(keycardai-mcp): advanced server handlers

## 0.2.0-keycardai-mcp (2025-09-16)


- feat(keycardai-mcp): auth provider implementation

## 0.1.0-keycardai-mcp (2025-09-07)
