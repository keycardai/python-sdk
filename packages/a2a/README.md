# keycardai-a2a

A2A (agent-to-agent) delegation SDK for Keycard. Wraps [a2a-sdk](https://github.com/a2aproject/A2A) 1.x with Keycard OAuth so an agent service can verify inbound bearer tokens, expose OAuth metadata for discovery, and call downstream services on behalf of the originating user via OAuth 2.0 token exchange (RFC 8693).

> **Preview.** This package is pre-1.0. APIs may change between minor versions.

## What's in here

- **`AgentServer`**, **`create_agent_card_server`**, **`serve_agent`**: compose a2a-sdk's standard route factories with `AuthenticationMiddleware` + `KeycardAuthBackend` from keycardai-starlette. The server exposes the standard A2A JSONRPC endpoint, the `.well-known/agent-card.json` discovery endpoint, and the OAuth metadata endpoints (RFC 9728 + RFC 8414).
- **`DelegationClient`**, **`DelegationClientSync`**: server-to-server token exchange helpers for calling other agent services on behalf of the original user.
- **`ServiceDiscovery`**: query an agent service's `.well-known/agent-card.json`.
- **`AgentServiceConfig`**: configuration container (identity, credentials, executor, capabilities).

There is **no** parallel `AgentExecutor` protocol or custom HTTP endpoint here. Customers implement [a2a-sdk's native `AgentExecutor`](https://github.com/a2aproject/A2A) (async, event-driven) directly, and the verified bearer token is propagated into a2a-sdk's `ServerCallContext.state` for use during execution.

## Installation

```bash
pip install keycardai-a2a
```

This pulls in `keycardai-oauth`, `keycardai-starlette`, `a2a-sdk[http-server]`, and `uvicorn`.

## Quick start

```python
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue_v2 import EventQueue

from keycardai.a2a import AgentServiceConfig, serve_agent


class EchoExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Read the verified bearer token (populated by keycardai-a2a's
        # context_builder) for any downstream delegation:
        access_token = context.call_context.state.get("access_token")

        text = context.get_user_input()
        # Publish a Message event back to the caller; see a2a-sdk docs for
        # the full set of events you can emit (TaskStatusUpdateEvent, etc).
        ...

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None


config = AgentServiceConfig(
    service_name="My Agent",
    client_id="...",
    client_secret="...",
    identity_url="https://my-agent.example.com",
    zone_id="your-zone-id",
    agent_executor=EchoExecutor(),
)

serve_agent(config)
```

## Relationship to other Keycard packages

- **`keycardai-oauth`**: OAuth 2.0 primitives used internally for token exchange and PKCE flows.
- **`keycardai-starlette`**: provides the `AuthenticationMiddleware` + `KeycardAuthBackend` that protect this package's JSONRPC mount.
- **`keycardai-mcp`**: sister package for MCP server protection. Same shape (Keycard auth + delegation), different protocol.

## History

This package was extracted from the original `keycardai-agents` package (KEP: Decompose keycardai-agents). The PKCE user-login client moved to `keycardai-oauth`; the CrewAI integration moves to a forthcoming `keycardai-crewai`; the `keycardai-agents` source directory is being archived.
