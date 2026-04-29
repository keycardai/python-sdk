# keycardai-crewai

CrewAI integration for [keycardai-a2a](../a2a). Use a CrewAI `Crew` as the agent body of a Keycard-protected A2A service, and give CrewAI agents tools that delegate work to other A2A services with the user's token exchanged via Keycard.

> **Preview.** This package is pre-1.0. APIs may change between minor versions.

## What's in here

Server-side:

- **`CrewAIExecutor`**: adapter that wraps a CrewAI `Crew` factory so it can be wired into `a2a-sdk`'s `DefaultRequestHandler` as the agent body.
- **`set_delegation_token(access_token)`**: stash the inbound bearer in a contextvar so synchronous CrewAI tool calls can pick it up at delegation time.

Client-side (CrewAI tools that delegate to other A2A services):

- **`get_a2a_tools(service_config, delegatable_services)`**: returns a list of `crewai.tools.BaseTool` instances, one per delegatable service. Each tool calls the target service via `keycardai-a2a`'s `DelegationClientSync.invoke_service`, exchanging the contextvar token along the way.
- **`create_a2a_tool_for_service(service_config, target_service_url)`**: single-tool variant for cases where the caller already knows the target URL.

## Installation

```bash
pip install keycardai-crewai
```

This pulls in `keycardai-a2a`, `crewai`, and (transitively) `keycardai-oauth` + `keycardai-starlette`.

## Quick start

In your A2A executor's `execute` method, call `set_delegation_token` with the verified bearer (read from `context.call_context.state["access_token"]`, populated by `keycardai.a2a.KeycardServerCallContextBuilder`) and then run your crew. CrewAI tools created via `get_a2a_tools` will pick the token up from the contextvar at invocation time.

```python
from a2a.server.agent_execution import AgentExecutor
from a2a.types import Message, MessageRole, Part

from keycardai.a2a import AgentServiceConfig
from keycardai.crewai import CrewAIExecutor, get_a2a_tools, set_delegation_token

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


class CrewExecutor(AgentExecutor):
    async def execute(self, context, event_queue):
        access_token = context.call_context.state.get("access_token")
        if access_token:
            set_delegation_token(access_token)

        crew_runner = CrewAIExecutor(make_crew)
        text = context.get_user_input()
        result = crew_runner.execute(text)

        message = Message(role=MessageRole.MESSAGE_ROLE_AGENT, parts=[Part(text=result)])
        await event_queue.enqueue_event(message)

    async def cancel(self, context, event_queue):
        return None
```

Compose this `CrewExecutor` with the `keycardai-a2a` primitives in your Starlette/FastAPI app — see [`packages/a2a/examples/keycard_protected_server`](../a2a/examples/keycard_protected_server) for the auth wiring.

## Relationship to other Keycard packages

- **`keycardai-a2a`**: provides the agent service primitives this package builds on. The `CrewAIExecutor` is fed into a2a-sdk's `DefaultRequestHandler`; the tools created by `get_a2a_tools` go through `keycardai-a2a`'s `DelegationClientSync`.
- **`keycardai-oauth`**: token exchange runs through `keycardai-oauth` under the hood, via `keycardai-a2a`'s delegation client.
- **`keycardai-starlette`**: the auth backend protecting the agent service mount lives here.
- **`keycardai-mcp`**: hosts a separate CrewAI integration for **MCP tools** (different protocol). That one stays in `keycardai-mcp` and is unrelated to this package.

## History

This package was extracted from the original `keycardai-agents` package (KEP: Decompose keycardai-agents). The PKCE user-login client moved to `keycardai-oauth`; the A2A delegation surface moved to `keycardai-a2a`; the `keycardai-agents` source directory is being archived.
