# keycard-protected-server example

A runnable Starlette app that hosts a Keycard-protected A2A agent service composed from `keycardai-a2a` primitives plus a2a-sdk's standard route factories. Useful as the copy-paste source for greenfield agent services.

If you already have an `a2a-sdk` server running, you do NOT want this whole composition. Take just the `Mount("/a2a", ...)` block plus the two `well_known_*` routes from `main.py:build_app` and add them to your existing app.

## What's wired

- `EchoExecutor`: an `AgentExecutor` subclass that returns the user's message text.
- `EagerKeycardAuthBackend(verifier)`: wraps Keycard's bearer verification in a backend that 401s on anonymous requests (the JSONRPC dispatcher has no per-route gate, so the middleware must be the gate).
- `KeycardServerCallContextBuilder()`: propagates the verified `KeycardUser` plus the bare access token into a2a-sdk's `ServerCallContext.state` so the executor can read `context.call_context.state["access_token"]` for delegated downstream calls.
- `build_agent_card_from_config(config)`: produces the 1.x protobuf `AgentCard` consumed by `create_agent_card_routes` and `DefaultRequestHandler`.
- `well_known_protected_resource_route` and `well_known_authorization_server_route` from keycardai-starlette: serve the RFC 9728 + RFC 8414 OAuth metadata endpoints.

## Run

```bash
export KEYCARD_CLIENT_ID="..."
export KEYCARD_CLIENT_SECRET="..."
export KEYCARD_ZONE_ID="..."
export IDENTITY_URL="https://your-agent.example.com"

uv run keycard-protected-server
# or:
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

## Endpoints

- `GET /.well-known/agent-card.json` (public): A2A discovery card.
- `GET /.well-known/oauth-protected-resource` (public): RFC 9728 metadata.
- `GET /.well-known/oauth-authorization-server` (public): RFC 8414 metadata.
- `POST /a2a/jsonrpc` (Keycard-protected): A2A JSONRPC dispatcher.

## Calling it

See `../a2a_jsonrpc_usage/main.py` for the client side: a manual httpx JSONRPC POST and an a2a-sdk 1.x `Client` flow, both with bearer auth.
