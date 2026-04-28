# keycardai-a2a

A2A (agent-to-agent) delegation SDK for Keycard. Build agent services that can be called by other agents while preserving the original user's identity and authorization context through OAuth 2.0 token exchange (RFC 8693).

> **Preview.** This package is pre-1.0. APIs may change between minor versions.

## What's in here

- **`AgentServer`**, **`create_agent_card_server`**, **`serve_agent`**: host an agent as an A2A-compatible service with Keycard OAuth protection on `/invoke` and the standard A2A JSONRPC endpoints.
- **`AgentExecutor`**, **`SimpleExecutor`**, **`LambdaExecutor`**: protocol + helpers for the unit of work the server invokes per request.
- **`KeycardToA2AExecutorBridge`**: bridges a Keycard executor to the A2A SDK's executor interface.
- **`DelegationClient`**, **`DelegationClientSync`**: server-to-server token exchange to call other agent services on behalf of the original user.
- **`ServiceDiscovery`**: discover agent service capabilities via `.well-known/agent-card.json`.
- **`AgentServiceConfig`**: configuration container for an agent service (identity, credentials, executor, capabilities).

## Installation

```bash
pip install keycardai-a2a
```

This installs `keycardai-oauth`, `keycardai-starlette`, FastAPI / Uvicorn, and the `a2a-sdk` as dependencies.

## Quick start

```python
from keycardai.a2a import AgentServiceConfig, serve_agent, SimpleExecutor

config = AgentServiceConfig(
    service_name="My Agent",
    client_id="...",
    client_secret="...",
    auth_server_url="https://your-zone.keycard.cloud",
    identity_url="https://my-agent.example.com",
    agent_executor=SimpleExecutor(lambda task, inputs: f"echoed: {task}"),
)

serve_agent(config)
```

## Relationship to other Keycard packages

- **`keycardai-oauth`**: OAuth 2.0 primitives used internally for token exchange and PKCE flows.
- **`keycardai-starlette`**: Starlette/FastAPI integration; provides the `KeycardAuthBackend` that protects this package's HTTP endpoints.
- **`keycardai-mcp`**: Sister package for MCP server protection. Same shape (Keycard auth + delegation), different protocol.

## History

This package was extracted from the original `keycardai-agents` package (KEP: Decompose keycardai-agents). The PKCE user-login client moved to `keycardai-oauth`; the CrewAI integration is in `keycardai-crewai`; the `keycardai-agents` source directory is being archived.
