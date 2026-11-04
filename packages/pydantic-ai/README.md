# keycardai-pydantic-ai

Delegated, per-tool-call access for [Pydantic AI](https://ai.pydantic.dev)
agents, with credentials brokered by [Keycard](https://keycard.ai). One
capability on the agent, one identity on the run's `deps`, one call inside each
tool to read the credential for this call. Nothing about Keycard reaches the
model or the tool schema.

## Install

```bash
pip install keycardai-pydantic-ai
```

Requires Python 3.11 or later (pydantic-ai-slim's floor) and depends on
`pydantic-ai-slim` and `keycardai-oauth` only; bring your own model extra
(`pydantic-ai-slim[anthropic]`, `[openai]`, ...).

## Quick start

```python
from pydantic_ai import Agent

from keycardai.pydantic_ai import Access, KeycardGrant, KeycardIdentity, get_access_context

CALENDAR = "https://www.googleapis.com/calendar/v3"

keycard = KeycardGrant(
    zone_url="https://your-zone.keycard.cloud",
    resources=[CALENDAR],
    client_id="your-agent",
    client_secret=...,
)

agent = Agent("anthropic:claude-sonnet-4-5", deps_type=KeycardIdentity, capabilities=[keycard])


@agent.tool_plain
def list_events(days_ahead: int = 0) -> str:
    """List the user's calendar events."""
    token = get_access_context().access(CALENDAR).access_token
    ...


agent.run_sync("What's on today?", deps=Access.on_behalf_of(caller_token))
```

## How it works

`KeycardGrant` is an agent capability. Before every tool call it resolves the
identity the run acts under, acquires a credential for the tool's resources from
your zone, and installs the result in a context variable for the duration of
the tool body. `get_access_context()` reads it, from async tools and from sync
tools (which the framework runs in a worker thread with the context copied).

The capability holds configuration only. Identity travels on the run.

## Identity on `deps`

`deps` is the framework's per-run data, so that is where identity lives:

```python
agent.run_sync(prompt, deps=Access.on_behalf_of(subject_token))  # user-facing
agent.run_sync(prompt, deps=Access.as_self())                      # background job
agent.run_sync(prompt, deps=Access.impersonate("alice@example.com"))
```

When your agent already has a deps object, give it a `keycard_identity` field:

```python
@dataclass
class Deps:
    db: Database
    keycard_identity: KeycardIdentity | None = None

agent = Agent(model, deps_type=Deps, capabilities=[keycard])
agent.run_sync(prompt, deps=Deps(db, keycard_identity=Access.on_behalf_of(token)))
```

Anything else is read with `identity_from=lambda ctx: ...`, and
`fallback_identity=` applies when nothing is found (a callable is resolved per
tool call, so a mid-run sign-in is picked up on resume). `RunContext.metadata`
is never read.

| Pattern | Grant | Audit actor |
|---|---|---|
| `Access.on_behalf_of(token)` | RFC 8693 token exchange | agent for user |
| `Access.as_self()` | client credentials | the application |
| `Access.impersonate(user)` | substitute-user token | agent as user |

## Errors are data, not exceptions

A failed acquisition never breaks the run. It is recorded on the
`AccessContext` the tool reads, per resource, so one denied resource does not
poison a granted one:

```python
access = get_access_context()
if access.has_error():                      # no identity, zone unreachable
    return f"Unavailable: {access.get_error()['message']}"
if access.has_resource_error(CALENDAR):     # this resource not granted
    return f"Not granted: {access.get_resource_error(CALENDAR)}"
token = access.access(CALENDAR).access_token  # raises ResourceAccessError only here
```

## Pausing for sign-in and consent

With `sign_in_url` and `authorization_url` set, a human flow pauses the run
through the framework's deferred tool approval instead of recording an error.
Add `DeferredToolRequests` to the agent's `output_type` and resume with
`DeferredToolResults`:

```python
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults

keycard = KeycardGrant(
    zone_url=..., resources=[CALENDAR], client_id=..., client_secret=...,
    sign_in_url="https://your-app.example/signin",
    authorization_url=lambda resources: f"https://your-app.example/authorize?r={resources[0]}",
)
agent = Agent(model, deps_type=KeycardIdentity, output_type=[str, DeferredToolRequests], capabilities=[keycard])

result = agent.run_sync(prompt, deps=identity)
while isinstance(result.output, DeferredToolRequests):
    for call in result.output.approvals:
        payload = result.output.metadata[call.tool_call_id]   # the pause payload
        show_user(payload["message"], payload.get("sign_in_url") or payload.get("authorization_url"))
    wait_for_the_user()
    result = agent.run_sync(
        message_history=result.all_messages(),
        deps=identity,                                       # deps are not in the history
        deferred_tool_results=result.output.build_results(approve_all=True),
    )
```

| Payload `type` | Fires when | Resume behavior |
|---|---|---|
| `sign_in_required` (`reason`: `missing_identity` or `subject_token_expired`) | no identity on the run, or its subject token has expired | identity is re-resolved, then the exchange runs |
| `authorization_required` (`resources`, `errors`) | identity present and valid, grant missing | the exchange is retried |

The payload is the same shape keycardai-langchain puts in its interrupt, so one
host renders both. The tool body never runs before the pause resolves. An
approved resume re-acquires once; if the grant is still missing the call ends
with a terminal `ToolFailed` carrying the same URL, never a second pause.
`ToolDenied` on resume skips the tool entirely. As-itself never pauses.

Expiry is detected locally (a decode-only check of the JWT's `exp`; the zone
stays the authority), so an expired session routes to sign-in rather than to
a consent page that cannot fix it.

`HandleDeferredToolCalls` from `pydantic_ai.capabilities` resolves the pause
inside the run when your host can complete the flow synchronously. Without
either it or `DeferredToolRequests` in `output_type`, the framework raises its
own error at the first pause.

### Without a pause: `defer_on_auth=False`

The call ends immediately with a `ToolFailed` whose message carries the sign-in
or consent URL, so the model relays it as text. Useful for hosts that cannot
resume a run.

## Using tools outside the agent

`get_access_context()` normally only works inside a run. `grant()` enters the
same access context explicitly, for code that calls a tool function without the
agent loop or needs a resource with no tool attached:

```python
with keycard.grant(Access.on_behalf_of(session_token)):
    return list_events(days_ahead=0)

with keycard.grant(Access.as_self(), resources=[LLM_KEY]) as access:
    key = access.access(LLM_KEY).access_token
```

`agrant()` is the async variant. Both accept `tool_name=` to apply that tool's
`tool_resources` override, or `resources=` to grant exactly the listed
resources (one or the other), and fall back to `fallback_identity` when no
identity is passed. There is no run to pause, so nothing defers here: failures
stay on the yielded `AccessContext`, exactly as tools see them.

## Per-tool resources and scopes

```python
KeycardGrant(
    zone_url=...,
    resources=[CALENDAR],                                   # default for every tool
    tool_resources={"post_message": [SLACK]},               # per-tool override, by tool name
    request_scopes={CALENDAR: ["calendar.events"]},
)
```

`tool_resources` also takes a callable `(tool_name) -> list[str] | None`, where
`None` means the default resources and `[]` acquires nothing for that tool.
`request_scopes` is the outbound scope requested from Keycard, for both the
exchange and the as-itself grant; it is distinct from any scope enforced on the
caller's inbound token.

## Authenticating without a static secret

`client_id` / `client_secret` is shorthand for a `ClientSecret` credential.
Pass `application_credential=` instead for the other credential types in
`keycardai.oauth.server.credentials` (workload identity, web identity), and
`client=` to inject an `AsyncClient` in tests.

## Testing

```python
from keycardai.pydantic_ai.testing import mock_access_context


def test_list_events():
    with mock_access_context(resource_tokens={CALENDAR: "test-token"}):
        assert list_events(days_ahead=0)
```

`mock_access_context(access_token=...)` serves one token for any resource, which
is convenient but cannot catch a mistyped resource URL. Pass `resource_tokens=`
when the test should assert which resource a tool reads; `resource_errors=` and
`error_message=` cover the failure paths, and `override_access_context` takes a
hand-built context. Pair it with the framework's `TestModel` or `FunctionModel`
and `models.ALLOW_MODEL_REQUESTS = False`.

The package's own test strategy, row by row, lives in [TESTING.md](TESTING.md).

## Limitations

Durable execution isn't supported yet. Under `TemporalDurability`,
`DBOSDurability` or any other durability capability, `KeycardGrant` raises a
configuration error before acquiring anything: capability hooks run inside the
durable container and deps are serialized into its history, so a token would be
replayed and a subject token persisted. Run such agents without the durability
capability for now.

Tools that also require approval (`requires_approval=True`, or any tool the
framework defers for approval) lose the consent pause. The framework asks for
that approval before `KeycardGrant` runs, and once it's given the re-run looks
the same as a resume after a Keycard pause. So if the grant is still missing,
the call ends with `ToolFailed` carrying the consent link instead of pausing for
consent. Grant access before approving such a call, or have the host handle the
`ToolFailed` text.

MCP toolsets need a per-run auth function (`MCPToolset(auth=...)`). That seam
comes in a follow-up release. Until then, exclude MCP tools from acquisition
with a `tool_resources` callable that returns `[]` for them.

One zone per capability.

## Examples

- [`examples/user_facing_agent`](examples/user_facing_agent): on-behalf-of with the pause and resume loop.
- [`examples/background_agent`](examples/background_agent): a scheduled as-itself digest.
