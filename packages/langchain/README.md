# keycardai-langchain

Keycard integration for LangChain agents. Every tool call gets a short-lived
credential brokered by Keycard, scoped to the identity the agent is acting for,
and recorded in the audit log.

Your tools never hold an API key, the model never sees a credential, and you do
not write an OAuth flow.

## Install

```bash
pip install keycardai-langchain
```

## Quick start

```python
from langchain.agents import create_agent
from langchain.tools import tool

from keycardai.langchain import (
    Access,
    KeycardGrantMiddleware,
    KeycardIdentity,
    get_access_context,
)

CALENDAR = "https://www.googleapis.com/calendar/v3"

keycard = KeycardGrantMiddleware(
    zone_url="https://your-zone.keycard.cloud",
    resources=[CALENDAR],
    client_id="your-agent",
    client_secret=...,
)


@tool
def list_events(days_ahead: int = 0) -> str:
    """List the user's calendar events."""
    token = get_access_context().access(CALENDAR).access_token
    ...


agent = create_agent(
    model,
    tools=[list_events],
    middleware=[keycard],
    context_schema=KeycardIdentity,
)

agent.invoke(
    {"messages": [...]},
    context=Access.on_behalf_of(caller_token),
)
```

That is the whole integration: one middleware in the agent's middleware list,
and one call inside each tool to read the credential for this call.

## How it works

`KeycardGrantMiddleware` implements LangChain's `wrap_tool_call` hook, so it
runs at the tool-call boundary. Before each tool executes it acquires tokens
for the declared resources under the identity of the run, then exposes the
result to the tool as an `AccessContext`.

Identity travels on the agent's own `context_schema`, and the
pause-for-authorization flow is a LangGraph interrupt.

The same middleware instance works under `create_agent`, a raw LangGraph graph,
and `create_deep_agent` (deep agents are built on the same middleware system).

## Access patterns

`KeycardIdentity` is the context schema for a run. Use an `Access.*` factory to
select the access pattern:

| Field | Factory | Meaning |
|---|---|---|
| `subject_token` | `Access.on_behalf_of(...)` | Exchange the caller's own token for resource tokens (RFC 8693). |
| `as_self=True` | `Access.as_self()` | Client-credentials grant under the agent's own application identity. No user anywhere. |
| `user_identifier` | `Access.impersonate(...)` | Substitute-user exchange, authenticated by the agent's credential. Forbidden by default; requires a zone policy. |

A run with no identity fails with a `missing_identity` error, or pauses with a
`sign_in_required` interrupt when `sign_in_url` is set. It never falls back to
the agent's own authority: acting as itself is always an explicit choice.

### On-behalf-of: a user-facing agent

The agent acts for the person in the chat. Their token is exchanged per tool
call, so every resource access is attributed to agent-for-user in the audit
log, and revoking the user's grant cuts the agent off immediately.

```python
from keycardai.langchain import Access

keycard = KeycardGrantMiddleware(
    zone_url="https://your-zone.keycard.cloud",
    resources=["https://www.googleapis.com/calendar/v3"],
    client_id="your-agent",
    client_secret=os.environ["KEYCARD_CLIENT_SECRET"],
    # Optional: pause the run in-chat instead of failing.
    sign_in_url="https://your-app.example/signin",
    authorization_url="https://your-app.example/authorize",
)

agent.invoke(
    {"messages": [...]},
    context=Access.on_behalf_of(caller_token),
)
```

Runnable version: [`examples/user_facing_agent`](examples/user_facing_agent).

### As itself: a background agent

No user in the loop: a scheduled digest, a queue worker, a monitor. The agent
authenticates as its own application and Keycard delivers whatever credential
the zone brokers for the resource, including vaulted secrets, so the worker's
environment holds no API keys and revocation lives in one place.

```python
from keycardai.langchain import Access

keycard = KeycardGrantMiddleware(
    zone_url="https://your-zone.keycard.cloud",
    resources=["https://api.github.com"],
    client_id="your-agent",
    client_secret=os.environ["KEYCARD_CLIENT_SECRET"],
)

agent.invoke(
    {"messages": [...]},
    context=Access.as_self(),
)
```

As-itself runs never pause on an interrupt, even when `sign_in_url` or
`authorization_url` is set: there is no user to send to a consent page, so a
denied grant stays on the `AccessContext` as an error for the tool and the
operator's logs.

Runnable version: [`examples/background_agent`](examples/background_agent).

### Impersonation: acting as a specific user without their token

The agent asks for tokens *as* a named user, authenticated only by its own
credential. This is the sharpest tool in the box and is forbidden by default;
it requires an explicit impersonation policy in the zone.

```python
from keycardai.langchain import Access

keycard = KeycardGrantMiddleware(
    zone_url="https://your-zone.keycard.cloud",
    resources=["https://www.googleapis.com/calendar/v3"],
    client_id="your-agent",
    client_secret=os.environ["KEYCARD_CLIENT_SECRET"],
)

agent.invoke(
    {"messages": [...]},
    context=Access.impersonate("user@example.com"),
)
```

### Authenticating without a static secret

`client_id` / `client_secret` is shorthand for a `ClientSecret` credential.
Every pattern also accepts an `application_credential`, so a deployed agent
can authenticate with a platform-signed OIDC token instead of holding a
secret:

```python
from keycardai.oauth.server import FileTokenSource, WorkloadIdentity

keycard = KeycardGrantMiddleware(
    zone_url="https://your-zone.keycard.cloud",
    resources=["https://api.github.com"],
    application_credential=WorkloadIdentity(FileTokenSource()),
)
```

`WorkloadIdentity` fetches the platform token per call and sends it as a
jwt-bearer client assertion; nothing long-lived sits in the environment.

### Identity without per-run context

For a deployed agent whose surface does not thread per-run context, set
`fallback_identity`. Pass a **callable** to resolve it per tool call, so a
sign-in that happens mid-conversation takes effect on resume without a restart:

```python
from keycardai.langchain import Access

keycard = KeycardGrantMiddleware(
    ...,
    fallback_identity=lambda: Access.on_behalf_of(session_token()),
)
```

## Errors are data, not exceptions

A missing grant is normal operation in a brokered setup, so the `AccessContext`
records failures instead of raising. Only `access(resource)` raises, and only
when you ask for a resource that has no token:

```python
access = get_access_context()
if access.has_errors():
    return f"Cannot reach the API yet: {access.get_errors()}"
token = access.access(CALENDAR).access_token
```

Returning a readable sentence beats raising here: in a chat UI a raised
exception reads as an internal error, when the truthful message is "you have
not granted this yet."

## Pausing for sign-in and consent

With `sign_in_url` and `authorization_url` set, the middleware pauses the run
with a LangGraph interrupt instead of failing, so the whole flow can live in
your chat surface:

```python
keycard = KeycardGrantMiddleware(
    zone_url=...,
    resources=[CALENDAR],
    sign_in_url="https://your-app.example/signin",
    authorization_url=lambda resources: f"https://your-app.example/authorize?r={resources[0]}",
)
```

| Payload `type` | Fires when | Resume behavior |
|---|---|---|
| `sign_in_required` | The run carries no identity, or its subject token has expired | Identity is re-resolved, then the exchange runs |
| `authorization_required` | Identity present and valid, grant missing | The exchange is retried |

Expiry is detected locally (a decode-only check of the JWT's `exp`; the zone
stays the authority on validity), so an expired session routes to sign-in
rather than to a consent page that cannot fix it. The `sign_in_required`
payload carries a `reason` field (`missing_identity` or
`subject_token_expired`) so a chat surface can word the prompt accordingly.

Both require a checkpointer. Two details worth knowing:

- **Resume needs no new token.** Consent changes the grant in the zone, not the
  token in your session, so the existing subject token exchanges successfully
  afterward.
- **Runtime context is not checkpointed.** A resume must re-supply identity,
  which a server does on every run anyway.

Scope granularity falls out of this for free: if a user has granted read but
not write, the read call succeeds and the write call is the one that pauses.

## MCP tools in the same agent

Agents commonly mix two kinds of access: REST tools whose credentials this
middleware brokers per tool call, and MCP servers that run their own
interactive OAuth through `keycardai-mcp`. Both can pause the run with the
same `authorization_required` payload, so your chat surface renders one auth
UX:

```python
from keycardai.mcp.client.integrations import langchain_agents

adapter = langchain_agents.LangChainClient(
    mcp_client,
    interrupt_on_auth=True,                          # opt in; off by default
    tool_allowlist=["list_issues", "create_issue"],  # keep the context small
)
```

Without `interrupt_on_auth` the MCP adapter keeps its own UX: it hands the
model a `request_authentication` tool instead of interrupting.

Three things to get right when the two are combined:

**MCP-backed tools exchange nothing.** The MCP server's OAuth grant belongs to
the user and that server, not to Keycard's exchange, so map those tools to an
empty resource list or the middleware will try to broker a token they do not
need:

```python
KeycardGrantMiddleware(
    zone_url=ZONE_URL,
    resources=[CALENDAR],                 # REST tools
    tool_resources={"call_mcp_tool": []}, # MCP-backed tool
    authorization_url=lambda resources: f"{BASE_URL}/authorize?r={resources[0]}",
)
```

**One callback route.** The MCP client's coordinator owns the redirect, and a
single route completes the flow for every user:

```python
coordinator = StarletteAuthCoordinator(
    redirect_uri=f"{BASE_URL}/auth/mcp/callback",
    backend=InMemoryBackend(),
)
manager = ClientManager(servers, auth_coordinator=coordinator)


async def mcp_callback(request):
    await coordinator.handle_completion(dict(request.query_params))
    return HTMLResponse("Authorized. Return to the chat and continue.")
```

**Per-user clients, tools bound after connect.** `manager.get_client(context_id=user_email)`
gives each user their own session; build the agent's MCP tools inside
`async with adapter:` (entering connects and discovers the authenticated
servers; `get_tools()` is empty without it) so the model sees the server's
real tool schemas (or use
`adapter.get_lazy_tools()` when the tool list must exist at import time). The
MCP client's [README](../mcp/src/keycardai/mcp/client/README.md#combining-mcp-tools-with-keycardai-langchain-grants)
covers that side in full.

## Using tools outside the agent

`get_access_context()` normally only works inside an agent run, because the
middleware sets the context at the tool-call boundary. For code that calls a
tool without the agent loop, `grant()` enters the same access context
explicitly. The motivating case is a UI panel served by the same governed
tool the agent uses in chat:

```python
from keycardai.langchain import Access

def dashboard_snapshot(session_token: str) -> str:
    with keycard.grant(Access.on_behalf_of(session_token)):
        return list_requests.invoke({})
```

It also serves resources that have no tool at all. Fetching a vaulted LLM
key under the agent's own identity, for example:

```python
from keycardai.langchain import Access

with keycard.grant(Access.as_self(), resources=[LLM_KEY]) as access:
    key = access.access(LLM_KEY).access_token
```

`agrant()` is the async variant. Both accept `tool_name=` to apply that
tool's `tool_resources` override, or `resources=` to grant exactly the
listed resources (one or the other, not both), and fall back to
`fallback_identity` when no identity is passed. There is no run to pause,
so nothing interrupts here: failures stay on the yielded `AccessContext`,
exactly as tools see them.

## Per-tool resources and scopes

```python
KeycardGrantMiddleware(
    zone_url=...,
    resources=[CALENDAR],                     # default for every tool
    tool_resources={"post_message": [SLACK]}, # per-tool override
    request_scopes={CALENDAR: ["calendar.events"]},
)
```

`request_scopes` is the **outbound** scope requested from Keycard, for both the
exchange and the as-itself grant. It is distinct from any scope enforced on the
caller's inbound token.

## Testing

```python
from keycardai.langchain.testing import mock_access_context


def test_list_events():
    with mock_access_context(resource_tokens={CALENDAR: "test-token"}):
        assert list_events.invoke({"days_ahead": 0})
```

`mock_access_context(access_token=...)` serves one token for any resource, which
is convenient but cannot catch a mistyped resource URL, since every lookup
succeeds. Pass `resource_tokens={...}` when the test should assert which
resource a tool reads. `resource_errors=` and `error_message=` cover the failure
paths, and `override_access_context` takes a hand-built context for full
control.

The package's own test strategy, row by row with coverage status, lives in
[TESTING.md](TESTING.md).

## Contract parity with TypeScript

The same integration exists for TypeScript as
[`@keycardai/langchain`](https://www.npmjs.com/package/@keycardai/langchain).
Same contract, idiomatic expression on each side: concepts, payload shapes,
defaults, and behaviors are identical; the spelling follows each language.

| Concept | Python (`keycardai-langchain`) | TypeScript (`@keycardai/langchain`) |
|---|---|---|
| Middleware | `KeycardGrantMiddleware(...)` | `keycardGrantMiddleware({ ... })` |
| Identity type | `KeycardIdentity` (dataclass, passed as `context_schema`) | `KeycardIdentity` / `keycardIdentitySchema` (carried by the middleware) |
| Identity factories | `Access.as_self()`, `Access.on_behalf_of(token)`, `Access.impersonate(user)` | `Access.asSelf()`, `Access.onBehalfOf(token)`, `Access.impersonate(user)` |
| Access context accessor | `get_access_context()` | `getAccessContext()` |
| Zone | `zone_url=` | `zoneUrl:` |
| Resource configuration | `resources=`, `tool_resources={tool: [...]}` | `resources:`, `toolResources: { tool: [...] }` |
| Outbound scopes | `request_scopes=` | `requestScopes:` |
| Credential | `application_credential=`, or `client_id=` / `client_secret=` | `applicationCredential:`, or `clientId:` / `clientSecret:` |
| Sign-in URL | `sign_in_url=` | `signInUrl:` |
| Authorization URL | `authorization_url=` (str or callable) | `authorizationUrl:` (string or function) |
| Fallback identity | `fallback_identity=` (value or callable) | `fallbackIdentity:` (value or function) |
| Escape hatch | `with keycard.grant(...) as access:` / `async with keycard.agrant(...)` | `await keycard.grant(options, (access) => ...)` |
| Testing seam | `mock_access_context(...)`, `override_access_context(...)` | `mockAccessContext(...)`, `overrideAccessContext(...)` |
| Error accessors | `has_errors()`, `get_errors()`, `get_resource_error(r)` | `hasErrors()`, `getErrors()`, `getResourceError(r)` |
| Ungranted read | raises `ResourceAccessError` | throws `ResourceAccessError` |
| Interrupt payloads | `sign_in_required` / `authorization_required`, snake_case fields | identical, snake_case fields preserved |
| Attempt cap | 3 acquisition attempts per tool call | 3 acquisition attempts per tool call |

Deliberate differences, where the language leaves no honest choice:

- **TypeScript's escape hatch is a callback, not a context manager.** There is
  no `with` there, so `grant` takes the body as a function and is always
  awaited, which is also why the TypeScript side has no `agrant`.
- **The TypeScript middleware owns the context schema.** LangChain 1.x
  middleware in JS declares `contextSchema` itself, so the agent does not pass
  one.
- **MCP composition is Python-only for now.** The section above pairs this
  middleware with `keycardai-mcp`; the TypeScript package deliberately has no
  MCP dependency in v1.

## A note on tool arguments

Give tools arguments that express **intent**, and keep configuration and clocks
out of the model's hands. A tool that accepts a resource URL will eventually be
called with a resource the model invented; a tool that accepts an absolute
timestamp will eventually be called with the wrong date. Prefer
`days_ahead: int` over an ISO string, and read the resource from configuration.
