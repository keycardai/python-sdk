# keycardai-crewai

CrewAI integration for [keycardai-a2a](../a2a). Use a CrewAI `Crew` as the agent body of a Keycard-protected A2A service, and give CrewAI agents tools that delegate work to other A2A services with the user's token exchanged via Keycard.

> **Preview.** This package is pre-1.0. APIs may change between minor versions.

## What's in here

Server-side:

- **`CrewAIExecutor`**: an `a2a-sdk` 1.x `AgentExecutor` subclass that runs a CrewAI `Crew`. Pass it directly to `a2a.server.request_handlers.DefaultRequestHandler(agent_executor=...)` — no wrapper needed.
- **`set_delegation_token(access_token)`**: stash an inbound bearer in the contextvar that synchronous CrewAI tools read at delegation time. `CrewAIExecutor` calls this for you; reach for it directly only when running a crew outside the executor.

Client-side (CrewAI tools that delegate to other A2A services):

- **`get_a2a_tools(service_config, delegatable_services)`**: returns a list of `crewai.tools.BaseTool` instances, one per delegatable service. Each tool calls the target service via `keycardai-a2a`'s `DelegationClientSync.invoke_service`, exchanging the contextvar token along the way.
- **`create_a2a_tool_for_service(service_config, target_service_url)`**: single-tool variant for cases where the caller already knows the target URL.

## Installation

```bash
pip install keycardai-crewai
```

This pulls in `keycardai-a2a`, `crewai`, and (transitively) `keycardai-oauth` + `keycardai-starlette`.

## Quick start

`CrewAIExecutor` is an `a2a-sdk` `AgentExecutor`, so it slots into `DefaultRequestHandler` the same way any other executor does. Build the Keycard-protected mount with `keycardai-a2a`'s primitives, drop `CrewAIExecutor(make_crew)` in as the executor, and you are done.

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
from keycardai.crewai import CrewAIExecutor, get_a2a_tools
from keycardai.oauth.server.credentials import ClientSecret
from keycardai.starlette import AuthProvider, KeycardAuthBackend, keycard_on_error
from keycardai.starlette.routers.metadata import (
    well_known_authorization_server_route,
    well_known_protected_resource_route,
)

config = AgentServiceConfig(
    service_name="My Crew",
    client_id="...",
    client_secret="...",
    identity_url="https://my-crew.example.com",
    zone_id="your-zone-id",
    capabilities=["orchestrator"],
)

# Build CrewAI tools that delegate to other A2A services
delegatable = [{"name": "Echo", "url": "https://echo.example.com", "description": "echoes input", "capabilities": ["echo"]}]
tools = await get_a2a_tools(config, delegatable_services=delegatable)


def make_crew():
    from crewai import Agent, Crew, Task

    orchestrator = Agent(role="Orchestrator", goal="Delegate to specialists", tools=tools)
    task = Task(description="{task}", agent=orchestrator, expected_output="result")
    return Crew(agents=[orchestrator], tasks=[task])


auth_provider = AuthProvider(
    zone_url=config.auth_server_url,
    server_name=config.service_name,
    server_url=config.identity_url,
    application_credential=ClientSecret((config.client_id, config.client_secret)),
)
verifier = auth_provider.get_token_verifier()

agent_card = build_agent_card_from_config(config)
request_handler = DefaultRequestHandler(
    agent_executor=CrewAIExecutor(make_crew),
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

`CrewAIExecutor.execute` reads the verified bearer from `context.call_context.state["access_token"]` (set by `KeycardServerCallContextBuilder`), calls `set_delegation_token` so synchronous CrewAI tools can read it, and runs `crew.kickoff()` on a worker thread via `asyncio.to_thread` to avoid blocking the event loop.

## Relationship to other Keycard packages

- **`keycardai-a2a`**: provides the agent service primitives this package builds on. `CrewAIExecutor` subclasses `a2a-sdk`'s `AgentExecutor` directly; the tools created by `get_a2a_tools` go through `keycardai-a2a`'s `DelegationClientSync`.
- **`keycardai-oauth`**: token exchange runs through `keycardai-oauth` under the hood, via `keycardai-a2a`'s delegation client.
- **`keycardai-starlette`**: the auth backend protecting the agent service mount lives here.
- **`keycardai-mcp`**: hosts a separate CrewAI integration for **MCP tools** (different protocol). That one stays in `keycardai-mcp` and is unrelated to this package.

## History

This package was extracted from the original `keycardai-agents` package (KEP: Decompose keycardai-agents). The PKCE user-login client moved to `keycardai-oauth`; the A2A delegation surface moved to `keycardai-a2a`; the `keycardai-agents` source directory is being archived.
