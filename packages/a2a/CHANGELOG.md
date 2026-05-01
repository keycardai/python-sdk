## 0.3.0-keycardai-a2a (2026-05-01)


- fix(keycardai-a2a)!: align DelegationClient with a2a-sdk 1.x JSONRPC dispatcher (ACC-231) (#107)
- DelegationClient was still speaking 0.x JSON-RPC (“message/send”, old envelope, no A2A-Version), so every call was rejected by real 1.x dispatchers before execution—breaking the entire keycardai-crewai delegation path. Fixed by upgrading to the 1.x contract (SendMessage, proper envelope + headers, new response shape) and adding a real dispatcher test to catch drift.

## 0.2.0-keycardai-a2a (2026-04-29)


- feat(keycardai-a2a): new package split from keycardai-agents (ACC-230) (#105)
- * feat(keycardai-a2a)!: new package split from keycardai-agents (ACC-230)
- Per the KEP "Decompose keycardai-agents", the A2A delegation surface moves
out of keycardai-agents into a new keycardai-a2a package, structurally
analogous to keycardai-mcp. Symbols available at the new namespace:
- - AgentServer, create_agent_card_server, serve_agent
- DelegationClient, DelegationClientSync
- AgentExecutor, SimpleExecutor, LambdaExecutor
- KeycardToA2AExecutorBridge
- ServiceDiscovery
- AgentServiceConfig
- The bearer middleware in server/app.py also migrates from the deprecated
BearerAuthMiddleware to the canonical KeycardAuthBackend +
AuthenticationMiddleware pattern from keycardai-starlette. The
keycardai-mcp dependency drops from this code path.
- Hard cut, no transitional bridge: ACC-232 confirms no known production
users of keycardai.agents.* paths.
- The PKCE user-login client (AgentClient) is dropped entirely. Its
capability already lives in keycardai-oauth as
keycardai.oauth.pkce.authenticate (ACC-229 / #101). The duplicate in
keycardai-agents is removed.
- What stays in keycardai-agents: the CrewAI integration only, with its
imports repointed at keycardai.a2a. ACC-231 will move it to a dedicated
keycardai-crewai; ACC-232 will archive the now-stub source directory.
- BREAKING:
- from keycardai.agents import AgentServer, DelegationClient, ...
  becomes from keycardai.a2a import ... .
- from keycardai.agents.client import AgentClient is gone; use
  keycardai.oauth.pkce.authenticate.
- keycardai-agents 0.3.0 ships with the dependency set reduced to
  keycardai-a2a + pydantic, mirroring its now-CrewAI-only scope.
- * fix(keycardai-a2a): apply migration edits to moved files (ACC-230)
- The first commit on this branch did the git mv's but staged the new
files only; the Edit-tool modifications to the moved files (import
rewrites, server/app.py bearer-wiring migration to KeycardAuthBackend +
AuthenticationMiddleware, example pyproject swap from keycardai-agents
to keycardai-a2a, conftest/tests import repoints, agents/__init__.py
trim, agents/pyproject dep set, crewai integration repoint, top-level
workspace sources, justfile test recipe, uv.lock refresh) all sat
uncommitted. CI rejected the prior commit because the example pyproject
still claimed keycardai-agents at packages/a2a/, conflicting with the
real keycardai-agents at packages/agents/ in the workspace graph.
- This commit lands the actual migration content. Tests pass locally:
keycardai-a2a 60/60, keycardai-agents 16/16, no regression in oauth /
starlette / mcp / mcp-fastmcp / fastmcp; ruff workspace check clean.
- * refactor(keycardai-a2a)!: wrap a2a-sdk 1.x, drop parallel surface (ACC-230)
- Aligns keycardai-a2a with the wrap-do-not-reinvent pattern used in
keycardai-mcp and keycardai-starlette: customers implement a2a-sdk native
async AgentExecutor directly; this package contributes only Keycard auth
wiring, OAuth metadata discovery, and convenience composition.
- Drops the parallel-protocol surface inherited from the keycardai-agents
move:
- AgentExecutor protocol (sync execute(task, inputs)) and SimpleExecutor /
  LambdaExecutor implementations
- KeycardToA2AExecutorBridge (the sync->async adapter that existed only to
  bridge our protocol to a2a-sdk)
- Custom POST /invoke endpoint with bespoke InvokeRequest / InvokeResponse
  Pydantic models alongside the standard A2A JSONRPC interface
- AgentServiceConfig.invoke_url (replaced by jsonrpc_url) and
  AgentServiceConfig.to_agent_card() (the 0.x dict-shape constructor)
- Migrates from a2a-sdk 0.x to 1.x natively:
- pyproject pin a2a-sdk[http-server]>=1.0
- Server composition uses route factories (create_jsonrpc_routes,
  create_agent_card_routes) instead of the gone A2AStarletteApplication
- Request handler is DefaultRequestHandlerV2 (alias DefaultRequestHandler)
- AgentCard built from 1.x protobuf schema (supported_interfaces,
  AgentCapabilities streaming/push_notifications/extended_agent_card)
- Example main.py uses a2a-sdk 1.x Client via create_client + A2ACardResolver
- Adds a KeycardServerCallContextBuilder that subclasses a2a-sdk default
builder and stashes the verified KeycardUser plus access_token into
ServerCallContext.state so AgentExecutor implementations can read the
bearer token from context.call_context.state["access_token"] for
downstream delegated token exchange.
- Tests:
- a2a 44/44 pass
- agents 16/16 pass with crewai extra
- ruff clean
- Note: the high-level @auth.grant decorator parity with keycardai-mcp is
not yet shipped here. Customers use DelegationClient (already in this
package) for explicit server-to-server delegation. The decorator port is
a follow-up.
- * fix(keycardai-a2a): address review findings on PR #105
- Three blockers caught in fresh-eyes review:
- 1. release.yml tag-trigger list was hardcoded; *-keycardai-a2a was missing,
   so the post-merge auto-bump would push the tag but the publish workflow
   would never trigger. Trusted Publisher being registered would have been
   moot.
- 2. DelegationClient.invoke_service hardcoded service_url + /invoke. The
   wrap-aligned server only exposes /a2a/jsonrpc; calling invoke_service
   against any 1.x server returned 404. The CrewAI delegation tool runs
   through this code path. Both async and sync variants now build a
   message/send JSONRPC envelope, POST it to /a2a/jsonrpc, and unwrap the
   result to preserve the legacy {result, delegation_chain} shape so the
   CrewAI integration keeps working unchanged.
- 3. discover_service in both DelegationClient and ServiceDiscovery validated
   the 0.x card shape (required_fields = [name, endpoints, auth]). The 1.x
   protobuf-derived JSON has none of endpoints / auth. Discovery against
   any 1.x server raised ValueError. Validation now requires only "name";
   transport / auth specifics live under supportedInterfaces and the OAuth
   metadata routes.
- Plus four important findings:
- 4. Test mocks across conftest.py, test_a2a_client.py, test_discovery.py,
   and test_crewai_a2a.py used the old shape (endpoints/auth keys). Tests
   passed because the validator wrongly accepted them. Mocks now use the
   1.x JSON shape (supportedInterfaces, capabilities object, skills with
   id/name).
- 5. A2AServiceClient and A2AServiceClientSync backward-compat aliases at
   the bottom of delegation.py contradicted the "hard cut, no transitional
   bridge" stance in the PR description. Removed.
- 6. TestJsonRpcAuthGate.test_jsonrpc_requires_authorization asserted
   status_code in (400, 401). 400 means the JSONRPC dispatcher saw the
   request and bailed on the body shape, not that the auth gate caught
   it. Pinned to == 401 with a WWW-Authenticate header check so the gate
   contract is enforced.
- 7. Zero coverage existed for _KeycardServerCallContextBuilder propagating
   the verified KeycardUser plus access_token into ServerCallContext.state.
   Added two unit tests that build the context directly: one with a
   KeycardUser asserting state["access_token"] is set, one with an
   UnauthenticatedUser asserting state["access_token"] is absent (so an
   executor reading it sees None rather than a stale token).
- Tests:
- a2a 47/47 (was 44; +3 new wrap-coverage tests)
- agents 16/16 with crewai extra
- ruff clean
- * refactor(keycardai-a2a)!: ship primitives, not a server abstraction (ACC-230)
- Per Kamil's review on PR #105: AgentServer / create_agent_card_server /
serve_agent presupposed customers want a fresh Starlette app dedicated to
the agent service. The wrap-don't-reinvent stance, taken seriously,
says: customers already have an a2a-sdk app in their head; we ship
primitives that slot Keycard auth into THAT, not a parallel server.
- Public surface change:
- Dropped:
  AgentServer, create_agent_card_server, serve_agent
Promoted to public (renamed off the underscore prefix):
  EagerKeycardAuthBackend
  KeycardServerCallContextBuilder
  build_agent_card_from_config
- AgentServiceConfig trimmed: dropped agent_executor (DefaultRequestHandler
takes its own), port and host (uvicorn's job), status_url (no /status
in the primitives layer).
- The composed-server flow moves to a runnable example at
packages/a2a/examples/keycard_protected_server/. README quickstart
rewritten to show primitive composition into an existing app; greenfield
users follow the example.
- Tests:
  a2a 44/44 (was 47; net -3 from dropping the /status endpoint tests
            and the port-validation test)
  agents 16/16 with crewai extra
  ruff clean
- This change is breaking, but the package is 0.1.0-pre-publish so no
customer is on these names yet.
- * fix(keycardai-a2a): ruff import-organization auto-fix
- * refactor(keycardai-starlette,keycardai-a2a): collapse EagerKeycardAuthBackend into KeycardAuthBackend kwarg (ACC-230)
- Per Kamil's second review observation on PR #105: with keycardai-a2a
now depending on keycardai-starlette, the question of WHERE these
primitives live matters. EagerKeycardAuthBackend was a 5-line subclass
that flipped one branch of KeycardAuthBackend.authenticate to raise on
missing Authorization, with no a2a-sdk specifics. The behavior is a
policy choice ("this mount requires auth"), not a different kind of
backend.
- Collapsed to a kwarg on the existing class:
-   KeycardAuthBackend(verifier)                              # default,
                                                            # mixed-route
  KeycardAuthBackend(verifier, require_authentication=True) # all-paths-protected
- The OAuth metadata bypass (RFC 9728 §2 / RFC 8414 §3) takes precedence
over the kwarg: even with require_authentication=True, requests to
/.well-known/oauth-* and /.well-known/jwks.json still pass through
anonymously per spec. New parametrized test asserts this.
- Net effect:
- One class instead of two; existing KeycardAuthBackend(verifier) callers
  unchanged.
- keycardai-a2a no longer ships EagerKeycardAuthBackend; the kwarg is
  used directly in tests, the example, and the README quickstart.
- Migration story is zero churn for existing users; new behavior is
  opt-in via the kwarg.
- Tests:
  starlette 40 passed (+2 new tests for the kwarg semantics)
  a2a 44 passed
  agents 16 passed
  ruff clean
