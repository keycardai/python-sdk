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
    context=KeycardIdentity(subject_token=caller_token),
)
```

That is the whole integration: one middleware in the agent's middleware list,
and one call inside each tool to read the credential for this call.

## How it works

`KeycardGrantMiddleware` implements LangChain's `wrap_tool_call` hook, so it
runs at the tool-call boundary. Before each tool executes it acquires tokens
for the declared resources under the identity of the run, then exposes the
result to the tool as an `AccessContext`.

The middleware is framework-shaped rather than Keycard-shaped: identity travels
on the agent's own `context_schema`, and the pause-for-authorization flow is a
LangGraph interrupt. Nothing is bolted onto the side of the framework.

The same middleware instance works under `create_agent`, a raw LangGraph graph,
and `create_deep_agent` (deep agents are built on the same middleware system).

## Access patterns

`KeycardIdentity` carries the identity for a run, and its fields select the
access pattern:

| Field | Pattern | Meaning |
|---|---|---|
| `subject_token` | on-behalf-of | Exchange the caller's own token for resource tokens (RFC 8693). |
| `as_self=True` | as itself | Client-credentials grant under the agent's own application identity. No user anywhere. |
| `user_identifier` | impersonation | Substitute-user exchange, authenticated by the agent's credential. Forbidden by default; requires a zone policy. |

A run with no identity at all is an error (or a sign-in interrupt), never a
silent fallback to the agent's own authority — acting as itself is always an
explicit choice.

### On-behalf-of: a user-facing agent

The agent acts for the person in the chat. Their token is exchanged per tool
call, so every resource access is attributed to agent-for-user in the audit
log, and revoking the user's grant cuts the agent off immediately.

```python
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
    context=KeycardIdentity(subject_token=caller_token),
)
```

Runnable version: [`examples/user_facing_agent`](examples/user_facing_agent).

### As itself: a background agent

No user in the loop — a scheduled digest, a queue worker, a monitor. The agent
authenticates as its own application and Keycard delivers whatever credential
the zone brokers for the resource, including vaulted secrets, so the worker's
environment holds no API keys and revocation lives in one place.

```python
keycard = KeycardGrantMiddleware(
    zone_url="https://your-zone.keycard.cloud",
    resources=["https://api.github.com"],
    client_id="your-agent",
    client_secret=os.environ["KEYCARD_CLIENT_SECRET"],
)

agent.invoke(
    {"messages": [...]},
    context=KeycardIdentity(as_self=True),
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
keycard = KeycardGrantMiddleware(
    zone_url="https://your-zone.keycard.cloud",
    resources=["https://www.googleapis.com/calendar/v3"],
    client_id="your-agent",
    client_secret=os.environ["KEYCARD_CLIENT_SECRET"],
)

agent.invoke(
    {"messages": [...]},
    context=KeycardIdentity(user_identifier="user@example.com"),
)
```

### Identity without per-run context

For a deployed agent whose surface does not thread per-run context, set
`fallback_identity`. Pass a **callable** to resolve it per tool call, so a
sign-in that happens mid-conversation takes effect on resume without a restart:

```python
keycard = KeycardGrantMiddleware(
    ...,
    fallback_identity=lambda: KeycardIdentity(subject_token=session_token()),
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
| `sign_in_required` | The run carries no identity | Identity is re-resolved, then the exchange runs |
| `authorization_required` | Identity present, grant missing | The exchange is retried |

Both require a checkpointer. Two details worth knowing:

- **Resume needs no new token.** Consent changes the grant in the zone, not the
  token in your session, so the existing subject token exchanges successfully
  afterward.
- **Runtime context is not checkpointed.** A resume must re-supply identity,
  which a server does on every run anyway.

Scope granularity falls out of this for free: if a user has granted read but
not write, the read call succeeds and the write call is the one that pauses.

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

## A note on tool arguments

Give tools arguments that express **intent**, and keep configuration and clocks
out of the model's hands. A tool that accepts a resource URL will eventually be
called with a resource the model invented; a tool that accepts an absolute
timestamp will eventually be called with the wrong date. Prefer
`days_ahead: int` over an ISO string, and read the resource from configuration.
