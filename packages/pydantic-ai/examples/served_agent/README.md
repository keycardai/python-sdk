# Served Agent (per-caller identity over HTTP)

A Pydantic AI calendar assistant served by FastAPI to a chat UI. Every request
carries the caller's Keycard bearer; keycardai-starlette verifies it against the
zone and the chat route hands the verified token to the run as its identity.
Each run then acts **on behalf of** the person who called, exchanging that
token per tool call for a Google Calendar token (RFC 8693). Nothing else
carries identity: not the request body, not a header the client picks.

The core of the route:

```python
auth = AuthProvider(zone_url=ZONE_URL, application_credential=credential, audience=AGENT_RESOURCE)
auth.install(app)

@app.post("/chat")
@requires("authenticated")
async def chat(request: Request) -> Response:
    user: KeycardUser = request.user
    return await AGUIAdapter.dispatch_request(
        request, agent=agent, deps=Access.on_behalf_of(user.access_token)
    )
```

`audience` is the agent's own resource, so a token minted for anything else is
rejected before the agent runs. A request without a bearer gets a 401 with the
RFC 6750 `WWW-Authenticate` challenge (and the RFC 9728 `resource_metadata`
URL), so an MCP-style client can discover the zone from the response.

## Why AG-UI rather than the Vercel AI adapter

Both adapters take per-request `deps` through `dispatch_request(...)`. They
differ in how KeycardGrant's pause reaches the client:

- AG-UI ends the paused run with `RUN_FINISHED` whose
  `outcome.interrupts[i].metadata` is the pause payload as KeycardGrant built
  it (`type`, `authorization_url`, `message`, `keycard_pause`), keyed to the
  tool call by `toolCallId` and `id` (`int-<tool_call_id>`). The client can
  render the consent link from that alone.
- The Vercel AI adapter emits a `tool-approval-request` chunk carrying only the
  `approvalId` and `toolCallId`; the payload does not travel, so a client would
  have to learn the URL some other way.

Neither adapter hands the pause metadata back on resume (see Limitations), so
the two are equal there, and AG-UI wins on the pause.

## Consent flow end to end

1. The client posts a `RunAgentInput` with the user's message. The model calls
   `list_events`; KeycardGrant finds no grant for the calendar resource and
   pauses the run.
2. The response stream ends with
   `RUN_FINISHED {"outcome": {"type": "interrupt", "interrupts": [{"id": "int-call-0", "toolCallId": "call-0", "metadata": {"type": "authorization_required", "authorization_url": "...", "message": "...", "keycard_pause": true}}]}}`.
   The client shows `metadata.authorization_url`.
3. The user opens the link and grants the agent access to their calendar in the
   zone.
4. The client posts again with the same thread, the message history including
   the assistant's tool call, and
   `"resume": [{"interruptId": "int-call-0", "status": "resolved", "payload": {"approved": true}}]`.
5. The adapter turns the resume entry into an approval for `call-0`; the tool
   re-runs, KeycardGrant exchanges the caller's token for the calendar token
   now that the grant exists, the tool reads it from `get_access_context()`,
   and the run finishes with the model's answer.

`tests/test_app.py` drives exactly this sequence through FastAPI's `TestClient`
with a stubbed verifier, a stub zone and a `FunctionModel`, and also checks the
401 challenge and that two callers each get a token exchanged for their own
subject token.

## Limitations

- The adapter builds the resume as `DeferredToolResults(approvals=...)` with
  no `metadata`, the same as a plain `build_results()`. KeycardGrant's own
  pause still resumes correctly (an approved re-run acquires the token or ends
  in `ToolFailed`), but a tool that also sets `requires_approval=True` cannot
  get a consent pause after its approval through either adapter: that needs
  the pause metadata echoed back, which `resume_results()` does and the
  adapters do not (Linear SDK-85, pydantic/pydantic-ai#12630).
- The adapters add `DeferredToolRequests` to the agent's output type only when
  the client sends frontend tools. This agent sets
  `output_type=[str, DeferredToolRequests]` itself so the pause can end the
  run; without it the paused run errors with "A deferred tool call was
  present, but `DeferredToolRequests` is not among output types".

## Keycard setup

1. Create a **zone** at [keycard.ai](https://keycard.ai) (or use an existing one).
2. Create an **application** for the agent and note its client ID and secret.
3. Create a **resource** for the agent itself (the audience the chat UI's
   tokens are minted for) and a **resource** for
   `https://www.googleapis.com/calendar/v3` with a Google OAuth credential
   provider.
4. Have the chat UI sign users in through the zone so each request carries a
   Keycard access token for the agent's resource.

## Run

```bash
export KEYCARD_ZONE_URL="https://your-zone.keycard.cloud"
export KEYCARD_CLIENT_ID="your-agent-client-id"
export KEYCARD_CLIENT_SECRET="your-agent-client-secret"
export KEYCARD_AGENT_RESOURCE="https://agent.example.com"
export ANTHROPIC_API_KEY="sk-ant-..."
# Optional. Consent links default to the zone URL.
# export KEYCARD_AUTHORIZATION_URL="https://your-app.example/authorize"

uv run main.py
```

Then point an AG-UI client (for example CopilotKit) at `http://127.0.0.1:8000/chat`
with the user's bearer, or post a `RunAgentInput` by hand:

```bash
curl -N http://127.0.0.1:8000/chat \
  -H "Authorization: Bearer $USER_TOKEN" -H "Content-Type: application/json" \
  -d '{"threadId":"t1","runId":"r1","messages":[{"id":"u1","role":"user","content":"What is on my calendar?"}],"tools":[],"context":[],"state":{},"forwardedProps":{}}'
```

## Test

```bash
uv sync --extra test
uv run pytest tests -q
```
