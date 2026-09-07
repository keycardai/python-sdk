# keycardai-a2a

Keycard auth primitives for [a2a-sdk](https://github.com/a2aproject/A2A) 1.x agent services. This package is glue, not a parallel server abstraction. Customers compose these primitives with a2a-sdk's standard route factories and request handler in their own Starlette / FastAPI app to get bearer token verification, OAuth metadata discovery, and OAuth 2.0 token exchange (RFC 8693) for downstream delegated calls.

## What's in here

Server-side wiring:

- **`KeycardServerCallContextBuilder`**: a `ServerCallContextBuilder` subclass. Pass to `a2a.server.routes.create_jsonrpc_routes`. Propagates the verified bearer token onto `ServerCallContext.state["access_token"]` so executors can read it for delegated downstream calls.
- **`build_agent_card_from_config(config)`**: produces a 1.x protobuf `AgentCard`. Pass to `a2a.server.routes.create_agent_card_routes` and `a2a.server.request_handlers.DefaultRequestHandler`.

For the auth backend itself, use `keycardai.starlette.KeycardAuthBackend(verifier, require_authentication=True)` on the JSONRPC mount. The kwarg flips the default mixed-route behavior to "every path on this mount needs auth," which matches the JSONRPC dispatcher's lack of a per-route gate.

Outbound delegation:

- **`DelegationClient`**, **`DelegationClientSync`**: server-to-server token exchange and JSONRPC invocation against another agent service.

Inbound discovery:

- **`ServiceDiscovery`**: query a remote agent service's `.well-known/agent-card.json` with caching.

Configuration:

- **`AgentServiceConfig`**: service identity + Keycard credentials + agent card metadata.

## Installation

```bash
pip install keycardai-a2a
```

This pulls in `keycardai-oauth`, `keycardai-starlette`, `a2a-sdk[http-server]>=1.0`.

## Quick start

You already have an `a2a-sdk` server. Add the Keycard-protected A2A mount to your existing Starlette / FastAPI app:

```python
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Mount

from keycardai.a2a import (
    AgentServiceConfig,
    KeycardServerCallContextBuilder,
    build_agent_card_from_config,
)
from keycardai.oauth.server.credentials import ClientSecret
from keycardai.starlette import AuthProvider, KeycardAuthBackend, keycard_on_error
from keycardai.starlette.routers.metadata import (
    well_known_authorization_server_route,
    well_known_protected_resource_route,
)

config = AgentServiceConfig(
    service_name="My Agent",
    client_id="...",
    client_secret="...",
    identity_url="https://my-agent.example.com",
    zone_id="your-zone-id",
    capabilities=["chat"],
)
auth_provider = AuthProvider(
    zone_url=config.auth_server_url,
    server_name=config.service_name,
    server_url=config.identity_url,
    # Bind accepted tokens to this service; leaving audience unset
    # disables the audience check entirely.
    audience=config.identity_url,
    application_credential=ClientSecret((config.client_id, config.client_secret)),
)
verifier = auth_provider.get_token_verifier()

agent_card = build_agent_card_from_config(config)
request_handler = DefaultRequestHandler(
    agent_executor=YourExecutor(),  # subclass of a2a.server.agent_execution.AgentExecutor
    task_store=InMemoryTaskStore(),
    agent_card=agent_card,
)

# Add these routes to your existing Starlette / FastAPI app:
your_app.routes.extend(create_agent_card_routes(agent_card=agent_card))
your_app.routes.append(well_known_protected_resource_route(
    issuer=config.auth_server_url,
    resource="/.well-known/oauth-protected-resource{resource_path:path}",
))
your_app.routes.append(well_known_authorization_server_route(
    issuer=config.auth_server_url,
    resource="/.well-known/oauth-authorization-server{resource_path:path}",
))
your_app.routes.append(Mount(
    "/a2a",
    routes=create_jsonrpc_routes(
        request_handler=request_handler,
        rpc_url="/jsonrpc",
        context_builder=KeycardServerCallContextBuilder(),
    ),
    middleware=[
        Middleware(
            AuthenticationMiddleware,
            backend=KeycardAuthBackend(verifier, require_authentication=True),
            on_error=keycard_on_error,
        ),
    ],
))
```

Inside your `AgentExecutor.execute(self, context, event_queue)`, read the bearer token via `context.call_context.state["access_token"]` and use it as the subject token in `keycardai-oauth`'s `TokenExchangeRequest` for downstream API calls.

For a runnable greenfield example (no existing app), see `examples/keycard_protected_server/`.

### Protocol version

A server composed this way speaks A2A protocol 1.0: `SendMessage` under an `A2A-Version: 1.0` header. All four Keycard SDKs send that generation, so no compatibility flag is needed between them:

- Python: `keycardai-a2a` 0.3.0 and later
- TypeScript: `@keycardai/a2a` 0.4.0 and later
- Go: `github.com/keycardai/go-sdk` v0.22.0 and later (`a2a.DelegationClient`)
- Ruby: `keycardai-a2a` 0.2.0 and later

A 0.3-generation caller (`message/send` with no `A2A-Version` header) is rejected by the a2a-sdk 1.x dispatcher with `-32601 MethodNotFound`. Such callers should upgrade; if one cannot, pass a2a-sdk's own `enable_v0_3_compat=True` to `create_jsonrpc_routes` yourself. This package no longer enables or recommends it.

## Relationship to other Keycard packages

- **`keycardai-oauth`**: OAuth 2.0 primitives used for token exchange and PKCE.
- **`keycardai-starlette`**: provides `AuthenticationMiddleware` + `KeycardAuthBackend` and the OAuth metadata route helpers used here.
- **`keycardai-mcp`**: sister package for MCP server protection. Same auth shape, different protocol.

## History

This package was extracted from the original `keycardai-agents` package (KEP: Decompose keycardai-agents). The PKCE user-login client moved to `keycardai-oauth`. The `keycardai-agents` package itself is archived.
