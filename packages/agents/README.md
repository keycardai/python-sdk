# KeycardAI Agents SDK

Keycard integration for AI agent frameworks (CrewAI) with secure MCP tool access.

## Overview

The `keycardai-agents` package provides clean integrations between Keycard's secure MCP client and popular agent frameworks. It follows these security principles:

1. **No Token Passing**: Agents never receive raw API tokens
2. **Fresh Tokens**: Each tool call fetches a fresh token through Keycard
3. **User Attribution**: All API calls are logged with user identity
4. **Auth Awareness**: Agents can request authentication when needed

## Installation

```bash
# For CrewAI support
pip install keycardai-agents[crewai]

# Or install all extras
pip install keycardai-agents[all]
```

## Quick Start - CrewAI

```python
import asyncio
from crewai import Agent, Crew, Task
from keycardai.agents.crewai_agents import create_client
from keycardai.mcp.client import Client as MCPClient

async def main():
    # 1. Configure MCP client to connect to your MCP servers
    mcp_config = {
        "mcpServers": {
            "github": {
                "command": "uvx",
                "args": ["keycardai-mcp-fastmcp", "--project-dir", "~/your-mcp-server"],
            }
        }
    }
    mcp_client = MCPClient(mcp_config)

    # 2. Create Keycard CrewAI adapter
    async with create_client(mcp_client) as client:
        # 3. Get Keycard-secured tools (NO TOKENS!)
        tools = await client.get_tools()
        auth_tools = await client.get_auth_tools()

        # 4. Create agents with secured tools
        agent = Agent(
            role="GitHub Expert",
            goal="Analyze pull requests",
            backstory=client.get_system_prompt("You are a code review expert"),
            tools=tools + auth_tools,  # Keycard-secured tools
        )

        # 5. Define and run tasks
        task = Task(
            description="Analyze PR #123 from repo owner/repo",
            expected_output="Detailed PR analysis",
            agent=agent,
        )

        crew = Crew(agents=[agent], tasks=[task])
        result = crew.kickoff()
        print(result)

asyncio.run(main())
```

## Architecture

### The Wrong Way (What NOT to Do)

```python
# ❌ WRONG: Passing tokens directly to agents
github_token = get_token_from_keycard()  # Token fetched once
agent = Agent(
    role="GitHub Expert",
    tools=create_github_tools(github_token),  # Token embedded in tools
)
# Problems:
# - Agent has unfettered access to token
# - Token can expire during execution
# - No per-call logging or authorization
# - Keycard is just an "auth orchestrator"
```

### The Right Way (Keycard Integration)

```python
# ✅ CORRECT: Tools call through MCP client
async with create_client(mcp_client) as client:
    tools = await client.get_tools()  # Tools with MCP client reference
    agent = Agent(role="GitHub Expert", tools=tools)
    # Each tool call:
    # 1. Agent invokes tool
    # 2. Tool calls mcp_client.call_tool()
    # 3. MCP client calls MCP server
    # 4. MCP server requests fresh token from Keycard
    # 5. API call is made with fresh token
    # 6. Call is logged with user attribution
```

## Key Components

### CrewAIClient

The main adapter class that wraps an MCP client and provides CrewAI-compatible tools.

```python
from keycardai.agents.crewai_agents import CrewAIClient, create_client

async with create_client(mcp_client) as client:
    # Get tools from authenticated MCP servers
    tools = await client.get_tools()  # List[BaseTool]

    # Get authentication request tools
    auth_tools = await client.get_auth_tools()  # List[BaseTool]

    # Get system prompt with auth context
    prompt = client.get_system_prompt("You are helpful")  # str
```

### Tool Conversion

MCP tools are automatically converted to CrewAI `BaseTool` instances:

- **Async Execution**: Tools use `async_run()` for async MCP calls
- **Schema Conversion**: JSON schemas converted to Pydantic models
- **Error Handling**: Errors are caught and returned as strings
- **Result Formatting**: MCP results formatted for agent consumption

### Authentication Flow

When an MCP server requires authentication:

1. **Auth Detection**: Client detects pending auth challenges on connect
2. **Auth Tool**: `request_authentication` tool is added to agent toolset
3. **Agent Request**: Agent can call the tool when user needs a service
4. **Auth Handler**: Configurable handler sends auth link to user
5. **Token Refresh**: After auth, tools automatically use new tokens

```python
# Custom auth handler example
from keycardai.agents.crewai_agents import AuthToolHandler

class SlackAuthHandler(AuthToolHandler):
    async def handle_auth_request(self, service, reason, challenge):
        # Send auth link to Slack
        await slack_client.post_message(
            channel=channel_id,
            text=f"Please authorize {service}: {challenge['auth_url']}"
        )
        return f"Authorization link sent to Slack channel"

async with create_client(mcp_client, auth_tool_handler=SlackAuthHandler()) as client:
    # Auth requests will be sent to Slack
    tools = await client.get_tools()
```

## Advanced Usage

### Multi-Agent Crews

```python
async with create_client(mcp_client) as client:
    tools = await client.get_tools()

    # Agent 1: Data fetcher
    fetcher = Agent(
        role="Data Fetcher",
        goal="Fetch information from APIs",
        tools=tools,
    )

    # Agent 2: Analyzer
    analyzer = Agent(
        role="Data Analyzer",
        goal="Analyze fetched data",
        tools=tools,  # Same tools, but each call is independently authorized
    )

    # Tasks with context passing
    fetch_task = Task(
        description="Fetch PR #123",
        agent=fetcher,
    )

    analyze_task = Task(
        description="Analyze the PR data",
        agent=analyzer,
        context=[fetch_task],  # Uses fetcher's output
    )

    crew = Crew(
        agents=[fetcher, analyzer],
        tasks=[fetch_task, analyze_task],
    )
    result = crew.kickoff()
```

### Custom System Prompts

```python
async with create_client(mcp_client) as client:
    # Default auth-aware prompt
    default_prompt = client.get_system_prompt("You are helpful")

    # Custom auth prompt
    client.auth_prompt = """
    **IMPORTANT**: Some services need authorization.
    Use the request_authentication tool with clear reasoning.
    """
    custom_prompt = client.get_system_prompt("You are helpful")
```

### Tool Caching

Tools are cached after first load for performance:

```python
async with create_client(mcp_client) as client:
    tools1 = await client.get_tools()  # Fetches from MCP servers
    tools2 = await client.get_tools()  # Returns cached tools

    # To force refresh, reconnect:
    await mcp_client.disconnect()
    await mcp_client.connect()
    tools3 = await client.get_tools()  # Fresh fetch
```

## Examples

Complete working examples with multi-agent crews, Keycard-secured API access, no token passing, and auth-aware agents will be added in future releases.

## Comparison with Other Integrations

### LangChain Integration

For LangChain, use the existing `keycardai-mcp` integration:

```python
from keycardai.mcp.client.integrations import langchain_agents

async with langchain_agents.create_client(mcp_client) as client:
    tools = await client.get_tools()  # LangChain StructuredTool objects
    # Use with LangChain agents
```

### OpenAI Agents Integration

For OpenAI Agents SDK, use the `keycardai-mcp` integration:

```python
from keycardai.mcp.client.integrations import openai_agents

async with openai_agents.create_client(mcp_client) as client:
    tools = await client.get_tools()  # OpenAI tool schemas
    # Use with OpenAI agents
```

## Framework Comparison

| Framework | Package | Tool Format | Agent Type |
|-----------|---------|-------------|------------|
| CrewAI | `keycardai-agents[crewai]` | `BaseTool` | Role-based |
| LangChain | `keycardai-mcp` | `StructuredTool` | Conversational |
| OpenAI Agents | `keycardai-mcp` | OpenAI schema | Function calling |

## Development

```bash
# Install in development mode
cd python-sdk/packages/agents
pip install -e ".[test]"

# Run tests
pytest

# Run type checking
mypy src/keycardai/agents

# Run linting
ruff check src/keycardai/agents
```

## License

MIT

## Links

- [GitHub Repository](https://github.com/keycardai/python-sdk)
- [Documentation](https://docs.keycardai.com)
- [Keycard Platform](https://keycard.ai)
