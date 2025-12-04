# Keycard MCP Client

A Python client for connecting to Model Context Protocol (MCP) servers with built-in OAuth 2.0 support.

## Overview

The Keycard MCP Client provides a simple, type-safe way to connect to MCP servers with automatic authentication handling. It supports multiple execution environments—from CLI applications to serverless functions—with a consistent API.

**Key Features:**
- 🔐 **Automatic OAuth 2.0 flows** (PKCE with dynamic client registration)
- 🏢 **Multi-user support** with isolated contexts
- ☁️ **Serverless-ready** with stateless execution support
- 🤖 **AI agent integrations** (OpenAI Agents, LangChain, CrewAI)
- 💾 **Flexible storage** (in-memory, SQLite, custom backends)
- 🔒 **Type-safe** with full protocol support

> **⚠️ Transport Support:** Currently, only the **`streamable_http`** transport is implemented. Other MCP transports (`stdio`, `sse`) are not yet supported. All examples in this README use HTTP transport.

---

## Quick Start

### Installation

**Base installation** (includes LangChain and OpenAI Agents support):

```bash
uv init --package mcp-cli && cd mcp-cli
uv add keycardai-mcp
```

**With CrewAI support** (requires additional dependencies):

```bash
uv add "keycardai-mcp[crewai]"
```

> **Why the `[crewai]` extra?** CrewAI requires `nest-asyncio` to bridge CrewAI's synchronous tool interface with MCP's asynchronous client. LangChain and OpenAI Agents have native async support and work with the base installation.

### Basic Usage (CLI/Desktop Apps)

For simple command-line tools or desktop applications, create a file `src/mcp_cli/__init__.py`:

```python
import asyncio
from keycardai.mcp.client import Client

# Configure your MCP server
servers = {
    "my-server": {
        "url": "http://localhost:7878/mcp",
        "transport": "http",  # Uses streamable_http transport
        "auth": {"type": "oauth"}
    }
}

async def run():
    # Create and use client (browser opens automatically for OAuth)
    async with Client(servers) as client:
        # List available tools with server information
        tools = await client.list_tools("my-server")
        print(f"Available tools: {len(tools)}")
        
        for tool_info in tools:
            print(f"  - {tool_info.tool.name} (from {tool_info.server})")
            print(f"    {tool_info.tool.description}")
        
        # Call the first tool (auto-discovers server if not specified)
        if tools:
            tool_name = tools[0].tool.name
            result = await client.call_tool(tool_name, {})
            print(f"\nResult: {result}")

def main():
    """Entry point for the CLI."""
    asyncio.run(run())
```

Run the CLI:

```bash
uv run mcp-cli
```

**What happens:**
1. Client reads OAuth config from server configuration
2. Client opens browser for authorization using Python's [`webbrowser`](https://docs.python.org/3/library/webbrowser.html) module
3. Blocks until user approves
4. Returns authenticated client ready to use

---

## Core Concepts

### Client & Session Architecture

The SDK provides multi-server connection management through a two-layer design:

**`Client` [`[source]`](client.py) - Multi-Server Orchestrator**
- Connects to and manages multiple MCP servers simultaneously
- Provides high-level API: `list_tools()`, `call_tool()`, `get_auth_challenges()`
- Auto-discovers which server has a tool when you call methods without specifying a server
- Handles connection lifecycle, authentication coordination, and storage management

**`Session` [`[source]`](session.py) - Per-Server Connection**
- Each MCP server gets its own `Session` instance (created and managed by `Client`)
- Wraps the upstream [`mcp.ClientSession`](https://github.com/modelcontextprotocol/python-sdk) from the Model Context Protocol library
- Adds authentication layer and storage scoping on top of the base protocol implementation
- **Tracks connection status lifecycle:** Use `session.status`, `session.is_operational`, `session.is_failed` to check connection state
- **Non-throwing connection behavior:** `connect()` sets status instead of raising exceptions for connection failures
- **Access directly for full protocol features:** Use `client.sessions[server_name]` to access all `ClientSession` methods

**Accessing the full protocol:**
```python
# High-level Client API (convenience methods)
tools = await client.list_tools()
result = await client.call_tool("my_tool", {})

# Direct Session access for features not yet abstracted by Client
session = client.sessions["my-server"]
resources = await session.list_resources()  # Full ClientSession API
prompts = await session.list_prompts()      # Access any ClientSession method

# Check session status
print(f"Status: {session.status}")          # SessionStatus enum
print(f"Operational: {session.is_operational}")  # Ready to use?
print(f"Failed: {session.is_failed}")       # In error state?
```

This design lets you use convenient abstractions while staying current with the latest MCP protocol features.

### What is an Auth Coordinator?

An **Auth Coordinator** manages authentication between your application and MCP servers. It handles whatever authentication method is configured in your server config (OAuth, API keys, or none).

For OAuth flows specifically, the coordinator manages how authorization consent is presented to users:
- **Local flows** (CLI/desktop apps) present authorization directly (e.g., by opening a browser) and block until user approval
- **Remote flows** (web apps/serverless) return authorization URLs for your application to present and handle callbacks asynchronously

The coordinator abstracts these differences, providing a consistent API regardless of your execution environment.

### Built-in Coordinators

The SDK provides **two built-in coordinators** that cover common use cases. You can also implement the [`AuthCoordinator`](auth/coordinators/base.py) interface to create custom coordinators for specialized environments.

| Coordinator | Environment | Behavior | Storage |
|-------------|-------------|----------|---------|
| [`LocalAuthCoordinator`](auth/coordinators/local.py) | CLI/Desktop | Opens browser, **blocks** until auth completes | In-memory by default |
| [`StarletteAuthCoordinator`](auth/coordinators/remote.py) | Web/Serverless | Returns auth URL, **non-blocking** | Configurable (memory/SQLite/custom) |

**Choosing a coordinator:**
- Running a script or desktop app? → [`LocalAuthCoordinator`](auth/coordinators/local.py)
- Building a web app or API? → [`StarletteAuthCoordinator`](auth/coordinators/remote.py)
- Running in Lambda/serverless? → [`StarletteAuthCoordinator`](auth/coordinators/remote.py) + [`SQLiteBackend`](storage/backends/sqlite.py)
- Need custom behavior? → Implement [`AuthCoordinator`](auth/coordinators/base.py) interface

### Connection Status & Error Handling

**Non-Throwing Behavior:** The `connect()` method does **not** raise exceptions for connection failures. Instead, sessions track their state through a status lifecycle. After calling `connect()`, check the session status to determine the outcome:

```python
await client.connect()

session = client.sessions["my-server"]

if session.is_operational:
    # Ready to use
    result = await client.call_tool("my_tool", {})
elif session.is_failed:
    # Handle connection failure
    print(f"Connection failed: {session.status}")
    if session.can_retry:
        await client.connect(server="my-server", force_reconnect=True)
elif session.requires_user_action:
    # Handle OAuth flow
    challenges = await client.get_auth_challenges()
```

**Key Properties:**
- `session.is_operational` - Ready to call tools
- `session.is_failed` - Connection failed
- `session.can_retry` - Failure is recoverable
- `session.requires_user_action` - Needs OAuth completion

See [`CONTRIBUTORS.md`](CONTRIBUTORS.md#session-status-lifecycle) for detailed status states and transitions.

### What is a Storage Backend?

A **Storage Backend** is where the client persists authentication tokens, OAuth state, and other data. The choice of storage backend depends on your execution environment and persistence requirements.

**Built-in storage backends:**

| Backend | Persistence | Use Case | Performance |
|---------|-------------|----------|-------------|
| [`InMemoryBackend`](storage/backends/memory.py) | ❌ Lost on restart | Development, stateful apps | ⚡ Fastest |
| [`SQLiteBackend`](storage/backends/sqlite.py) | ✅ Persists to disk/cloud | Production, serverless | ⚡ Fast |

**Choosing a storage backend:**
- Development or testing? → [`InMemoryBackend`](storage/backends/memory.py)
- Web app with persistent process? → [`InMemoryBackend`](storage/backends/memory.py) (fast) or [`SQLiteBackend`](storage/backends/sqlite.py) (survives restarts)
- Serverless/Lambda? → [`SQLiteBackend`](storage/backends/sqlite.py) (or custom DynamoDB/Redis backend)
- Need custom storage? → Implement `StorageBackend` interface

---

### What is a ClientManager?

A **ClientManager** [`[source]`](manager.py) is a higher-level interface for managing multiple MCP clients in multi-user or multi-tenant applications. Instead of creating individual `Client` instances manually, the `ClientManager` handles client lifecycle and provides automatic isolation between users.

**Key features:**
- **User isolation** - Each user gets their own client instance with isolated storage namespace
- **Client caching** - Reuses existing client instances for the same user (avoids redundant connections)
- **Automatic context management** - Creates and manages `Context` objects with proper user IDs
- **Shared coordinator** - All clients share the same auth coordinator (efficient for multi-user apps)

**When to use:**
- **Multi-user web apps** - Each user needs their own authenticated MCP connections
- **Multi-tenant applications** - Different tenants/organizations need isolated access
- **Serverless with multiple users** - Lambda/Cloud Functions handling requests from different users


### Event Notifications

**Event Notifications** allow you to receive callbacks when authentication events occur. This is useful for event-driven architectures where you need to react to authentication completions (e.g., notifying users, logging, triggering workflows).

**How it works:**
1. Create a class with an `async def on_completion_handled(self, event: CompletionEvent)` method
2. Register it with the coordinator using `coordinator.subscribe(your_subscriber)`
3. When OAuth completes, your subscriber receives a `CompletionEvent` with metadata

**CompletionEvent includes:**
- `context_id` - User/context that completed auth
- `server_name` - Which MCP server was authenticated
- `metadata` - Custom metadata you attached to the auth flow
- `result` - Completion result details

**When to use:**
- **Chat/messaging bots** - Notify users in Slack/Discord when auth completes
- **Webhooks** - Trigger external systems after authentication
- **Analytics/logging** - Track authentication events
- **Workflow automation** - Start processes after auth succeeds

**Example:**

```python
from keycardai.mcp.client.auth.events import CompletionEvent

class LoggingSubscriber:
    async def on_completion_handled(self, event: CompletionEvent):
        print(f"✅ Auth completed for {event.context_id} on {event.server_name}")
        # Access metadata: event.metadata
        # Trigger workflows, send notifications, log to database, etc.

coordinator = StarletteAuthCoordinator(...)
coordinator.subscribe(LoggingSubscriber())
```

See **Use Case 3: Event-Driven / Metadata Propagation** below for a complete example.

---

## Use Cases

### 1. CLI Applications

**Scenario:** Python script that needs to call MCP tools.

**Features used:**
- `LocalAuthCoordinator` (blocking OAuth flow)
- `InMemoryBackend` (fast, ephemeral storage)
- `Client` with context manager

```python
import asyncio
from keycardai.mcp.client import Client, LocalAuthCoordinator, InMemoryBackend

# Configure your MCP servers
servers = {
    "my-server": {
        "url": "http://localhost:7878/mcp",
        "transport": "http",
        "auth": {"type": "oauth"}
    }
}

async def run():
    # LocalAuthCoordinator handles browser-based OAuth
    coordinator = LocalAuthCoordinator(
        backend=InMemoryBackend(),
        host="localhost",
        port=8888,
        callback_path="/oauth/callback"
    )
    
    async with Client(servers, auth_coordinator=coordinator) as client:
        # OAuth happens automatically when connecting
        # Browser opens, you approve, then script continues
        
        # Check connection status (connection failures don't raise exceptions)
        session = client.sessions["my-server"]
        if not session.is_operational:
            print(f"Server not available: {session.status}")
            return
        
        # List available tools with server information
        tools = await client.list_tools("my-server")
        print(f"Available tools: {len(tools)}")
        
        for tool_info in tools:
            print(f"  - {tool_info.tool.name} (from {tool_info.server})")
            print(f"    {tool_info.tool.description}")
        
        # Call the first tool (auto-discovers server if not specified)
        if tools:
            tool_name = tools[0].tool.name
            result = await client.call_tool(tool_name, {})
            print(f"\nResult: {result}")

def main():
    """Entry point for the CLI."""
    asyncio.run(run())
```

#### Manual Browser Control (Non-Blocking)

If you prefer to control when the browser opens or want a non-blocking flow:

```python
import asyncio
from keycardai.mcp.client import Client, LocalAuthCoordinator, InMemoryBackend

servers = {
    "my-server": {
        "url": "http://localhost:7878/mcp",
        "transport": "http",
        "auth": {"type": "oauth"}
    }
}

async def run():
    # Disable auto-open browser and blocking behavior
    coordinator = LocalAuthCoordinator(
        backend=InMemoryBackend(),
        host="localhost",
        port=8888,
        callback_path="/oauth/callback",
        auto_open_browser=False,      # Don't auto-open browser
        block_until_callback=False    # Return immediately instead of blocking
    )
    
    async with Client(servers, auth_coordinator=coordinator) as client:
        # Context manager automatically connects to all servers
        # Connection failures are communicated via status, not exceptions
        
        session = client.sessions["my-server"]
        
        # Check if authentication is required
        if session.requires_user_action:  # status == AUTH_PENDING
            auth_challenges = await client.get_auth_challenges()
            if auth_challenges:
                auth_url = auth_challenges[0].get("authorization_url")
                print(f"\n🔐 Authentication required!")
                print(f"Please visit: {auth_url}\n")
                
                # Wait for user to complete auth in browser
                # Session automatically reconnects when OAuth completes (no manual reconnect needed!)
                while session.requires_user_action:
                    await asyncio.sleep(1)
        
        # Session is now connected automatically after auth completion
        if not session.is_operational:
            print(f"Failed to connect: {session.status}")
            return
        
        # Now authenticated - use the tools
        tools = await client.list_tools("my-server")
        print(f"Available tools: {len(tools)}")

def main():
    asyncio.run(run())
```

---

### 2. Web Applications

**Scenario:** Web app with multiple users, each with their own MCP connections.

**Features used:**
- `StarletteAuthCoordinator` (non-blocking OAuth flow)
- `ClientManager` (multi-user support)
- `InMemoryBackend` (fast access for stateful apps)
- `get_auth_challenges()` (check for pending auth)

```bash
uv init --package mcp-web && cd mcp-web
uv add keycardai-mcp starlette uvicorn
```

```python
from starlette.applications import Starlette
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route
import uvicorn

from keycardai.mcp.client import ClientManager, StarletteAuthCoordinator, InMemoryBackend

# Configure servers
servers = {
    "my-server": {
        "url": "http://localhost:7878/mcp",
        "transport": "http",
        "auth": {"type": "oauth"}
    }
}

# Create coordinator (shared across all users)
coordinator = StarletteAuthCoordinator(
    redirect_uri="http://localhost:8000/oauth/callback",
    backend=InMemoryBackend()
)

# Create client manager (shared across all users)
client_manager = ClientManager(
    servers,
    auth_coordinator=coordinator,
    storage_backend=InMemoryBackend()
)

# API endpoint: Call MCP tool
async def call_tool(request):
    user_id = request.path_params.get("user_id", "demo_user")
    
    # Get or create client for this user
    client = await client_manager.get_client(user_id)
    # Note: connect() does not raise exceptions - check status instead
    await client.connect()
    
    session = client.sessions["my-server"]
    
    # Check connection status
    if not session.is_operational:
        # Check if user needs to authorize
        if session.requires_user_action:
            pending_auth = await client.get_auth_challenges()
            if pending_auth:
                auth_url = pending_auth[0]["authorization_url"]
                return HTMLResponse(f'<a href="{auth_url}" target="_blank">Click to authorize</a>')
        
        # Connection failed
        return JSONResponse({
            "status": "error",
            "message": f"Server unavailable: {session.status.value}"
        })
    
    # User is authorized - list and call first tool
    tools = await client.list_tools("my-server")
    if tools:
        tool_name = tools[0].tool.name
        result = await client.call_tool(tool_name, {})
        return JSONResponse({
            "status": "success",
            "tool": tool_name,
            "result": str(result)
        })
    
    return JSONResponse({"status": "error", "message": "No tools available"})

# OAuth callback endpoint
async def oauth_callback(request):
    params = dict(request.query_params)
    await coordinator.handle_completion(params)
    return JSONResponse({"status": "Authorization complete! You can close this window."})

# Create Starlette app
app = Starlette(routes=[
    Route("/users/{user_id}/tool", call_tool),
    Route("/oauth/callback", oauth_callback),
])

def main():
    """Entry point for the web server."""
    print("\n🌐 MCP Web Server starting...")
    print("📋 Test the tool at: http://localhost:8000/users/alice/tool")
    print("🔗 OAuth callback: http://localhost:8000/oauth/callback\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

Run the web server:

```bash
uv run mcp-web
```

Then visit: `http://localhost:8000/users/demo_user/tool`

Follow the authorization link, and then refresh the page. It should show the tool call result now. 

---

### 3. Event-Driven / Metadata Propagation

**Scenario:** Bot that tracks request context through OAuth flow and sends notifications.

**Features used:**
- Context metadata (attach custom data to auth flows)
- Event notifications (`on_completion_handled` callback)
- Metadata propagation through OAuth lifecycle

```bash
uv init --package mcp-bot && cd mcp-bot
uv add keycardai-mcp starlette uvicorn
```

Create `src/mcp_bot/__init__.py`:

```python
from starlette.applications import Starlette
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route
import uvicorn

from keycardai.mcp.client import ClientManager, StarletteAuthCoordinator, InMemoryBackend
from keycardai.mcp.client.auth.events import CompletionEvent

# Configure servers
servers = {
    "my-server": {
        "url": "http://localhost:7878/mcp",
        "transport": "http",
        "auth": {"type": "oauth"}
    }
}

# Event subscriber that logs auth completions with metadata
class LoggingSubscriber:
    async def on_completion_handled(self, event: CompletionEvent):
        print(f"\n✅ Auth completed!")
        print(f"   State: {event.state}")
        print(f"   Success: {event.success}")
        
        # Access custom metadata that was attached to the request
        request_id = event.metadata.get("request_id")
        source = event.metadata.get("source")
        print(f"   Request ID: {request_id}")
        print(f"   Source: {source}")
        
        # Here you could: send Slack notification, trigger webhook, log to database, etc.

# Setup coordinator and subscribe to events
coordinator = StarletteAuthCoordinator(
    redirect_uri="http://localhost:8000/oauth/callback",
    backend=InMemoryBackend()
)
coordinator.subscribe(LoggingSubscriber())

client_manager = ClientManager(servers, auth_coordinator=coordinator)

# API endpoint with metadata tracking
async def call_tool(request):
    user_id = request.query_params.get("user", "demo_user")
    request_id = request.query_params.get("request_id", "req_001")
    
    # Create client WITH metadata - this flows through to subscriber
    client = await client_manager.get_client(
        context_id=user_id,
        metadata={
            "request_id": request_id,
            "source": "api",
            "endpoint": "/tool"
        }
    )
    await client.connect()
    
    pending_auth = await client.get_auth_challenges()
    if pending_auth:
        auth_url = pending_auth[0]["authorization_url"]
        return HTMLResponse(f'<a href="{auth_url}" target="_blank">Click to authorize</a>')
    
    # Call tool
    tools = await client.list_tools("my-server")
    if tools:
        result = await client.call_tool(tools[0].tool.name, {})
        return JSONResponse({"status": "success", "result": str(result)})
    
    return JSONResponse({"status": "error", "message": "No tools available"})

async def oauth_callback(request):
    await coordinator.handle_completion(dict(request.query_params))
    # LoggingSubscriber.on_completion_handled() is called here with metadata!
    return JSONResponse({"status": "complete"})

app = Starlette(routes=[
    Route("/tool", call_tool),
    Route("/oauth/callback", oauth_callback),
])

def main():
    """Entry point for the bot server."""
    print("\n🤖 MCP Bot Server starting...")
    print("📋 Test the bot at: http://localhost:8000/tool?user=demo_user&request_id=req_001")
    print("🔗 OAuth callback: http://localhost:8000/oauth/callback\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

Run the bot server:

```bash
uv run mcp-bot
```

Then visit: `http://localhost:8000/tool?user=alice&request_id=req_123`

Follow the authorization link, and then refresh the page. It should show the tool call result now. When OAuth completes, the subscriber receives the metadata and can trigger notifications, webhooks, or other workflows.

---

## AI Agent Integrations

The MCP client provides integrations for popular AI agent frameworks. Each framework has different async support:

| Framework | Installation | Extra Required? | Why |
|-----------|-------------|-----------------|-----|
| **LangChain** | `uv add keycardai-mcp langchain` | No | Native async support via `coroutine=` parameter |
| **OpenAI Agents** | `uv add keycardai-mcp openai-agents` | No | Native async support |
| **CrewAI** | `uv add "keycardai-mcp[crewai]"` | **Yes** | Requires `nest-asyncio` for sync/async bridge |

> **Technical Note:** CrewAI tools use synchronous methods (`_run()`), while MCP clients are async. The `[crewai]` extra includes `nest-asyncio` to enable nested event loops for seamless integration.

---

### How Authentication Works with AI Agents

The MCP client provides AI framework integrations that automatically handle authentication flows. Here's how it works:

**1. Auth Detection**

When you wrap the MCP client with `LangChainClient` or `OpenAIAgentsClient`, it:
- Connects to all configured MCP servers
- Detects which servers require authentication (via `get_auth_challenges()`)
- Identifies which servers are already authenticated

**2. Dynamic System Prompt**

The integration's `get_system_prompt()` method augments your base instructions with authentication awareness:

```python
system_prompt = client.get_system_prompt("You are a helpful assistant")
```

If services need auth, the prompt automatically includes instructions like:

```
**AUTHENTICATION STATUS:**
The following services require user authorization: slack-server, google-server

**IMPORTANT:** When the user requests an action that requires one of these services:
1. Call the `request_authentication` tool with:
   - service: The service name (e.g., "slack-server")
   - reason: Brief explanation (e.g., "To send messages to Slack channels")
2. The tool will initiate the authorization flow and send the auth link to the user
3. Inform the user that you've initiated authorization and they should check for the link
4. After the user authorizes, you will automatically gain access to use that service

**Note:** You already have access to: calendar-server
```

This prompting strategy guides the agent to:
- Recognize when authentication is needed
- Call the authentication tool proactively
- Provide clear explanations to users about why auth is required

**3. Auth Request Tool**

The integration provides a `request_authentication` tool via `get_auth_tools()`:

```python
tools = await client.get_auth_tools()  # Returns [request_authentication] if auth needed
```

When the agent calls this tool:
1. It passes the service name and a user-friendly reason
2. The `AuthToolHandler` receives the auth challenge details
3. The handler sends the authorization link to the user (via Slack DM, console, email, etc.)
4. User completes OAuth flow
5. Agent automatically gains access to the newly authenticated service's tools

**4. Customizable Auth Handlers**

Different environments require different auth delivery methods:

- **`DefaultAuthToolHandler`**: Returns formatted message for agent to display (fallback)
- **`ConsoleAuthToolHandler`**: Prints auth links to terminal (CLI apps)
- **`SlackAuthToolHandler`**: Sends auth links as Slack messages (Slack bots)
- **Custom**: Subclass `AuthToolHandler` for email, webhooks, web UI, etc.

Example with Slack:

```python
from keycardai.mcp.client.integrations.auth_tools import SlackAuthToolHandler

handler = SlackAuthToolHandler(
    slack_client=client,
    channel_id=channel_id,
    thread_ts=thread_ts
)

async with LangChainClient(mcp_client, auth_tool_handler=handler) as client:
    # Auth links sent directly to Slack thread, bypassing agent message flow
    ...
```

**Result**: Users get a seamless experience where the agent intelligently requests authentication only when needed, with authorization links delivered through the appropriate channel for their environment.

---

### LangChain

```bash
uv init --package mcp-langchain && cd mcp-langchain
uv add keycardai-mcp langchain langchain-openai
```

Set your OpenAI API key:

```bash
export OPENAI_API_KEY="sk-..."
```

Create `src/mcp_langchain/__init__.py`:

```python
import asyncio
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from keycardai.mcp.client import Client
from keycardai.mcp.client.integrations.langchain_agents import LangChainClient

servers = {
    "my-server": {
        "url": "http://localhost:7878/mcp",
        "transport": "http",
        "auth": {"type": "oauth"}
    }
}

async def run():
    async with Client(servers) as mcp_client:
        # Wrap MCP client for LangChain
        async with LangChainClient(mcp_client) as langchain_client:
            # Get tools converted to LangChain format
            tools = await langchain_client.get_tools()
            print(f"Available tools: {len(tools)}")
            
            # Get system prompt
            system_prompt = langchain_client.get_system_prompt(
                "You are a helpful assistant with access to MCP tools."
            )
            
            # Create LangChain agent
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
            agent = create_agent(
                llm,
                tools=tools,
                system_prompt=system_prompt
            )
            
            # Agent can now call MCP tools
            print("\nAsking agent to use available tools...")
            response = await agent.ainvoke({
                "messages": [{"role": "user", "content": "List the available tools and use one of them"}]
            })
            print(f"\nAgent response: {response['messages'][-1].content}")

def main():
    """Entry point for LangChain agent."""
    asyncio.run(run())
```

Run the LangChain agent:

```bash
uv run mcp-langchain
```

### OpenAI Agents SDK

Install dependencies:

```bash
uv add keycardai-mcp openai-agents
```

Set your OpenAI API key:

```bash
export OPENAI_API_KEY="sk-..."
```

Create `src/mcp_openai/__init__.py`:

```python
import asyncio
from agents import Agent, Runner

from keycardai.mcp.client import Client
from keycardai.mcp.client.integrations.openai_agents import OpenAIAgentsClient

servers = {
    "my-server": {
        "url": "http://localhost:7878/mcp",
        "transport": "http",
        "auth": {"type": "oauth"}
    }
}

async def run():
    async with Client(servers) as mcp_client:
        # Wrap MCP client for OpenAI Agents
        async with OpenAIAgentsClient(mcp_client) as openai_client:
            # Get system prompt with MCP context
            system_prompt = openai_client.get_system_prompt(
                "You are a helpful assistant with access to MCP tools."
            )
            
            # Get MCP servers for agent
            mcp_servers = openai_client.get_mcp_servers()
            print(f"MCP servers available: {len(mcp_servers)}")
            
            # Create OpenAI Agent
            agent = Agent(
                name="mcp_assistant",
                instructions=system_prompt,
                mcp_servers=mcp_servers
            )
            
            # Agent can now call MCP tools
            print("\nAsking agent to use available tools...")
            response = await Runner.run(
                agent,
                "What tools do you have access to? Use one of them."
            )
            print(f"\nAgent response: {response.final_output}")

def main():
    """Entry point for OpenAI agent."""
    asyncio.run(run())
```

Run the OpenAI agent:

```bash
uv run mcp-openai
```

### CrewAI

Install dependencies:

```bash
uv add "keycardai-mcp[crewai]"
```

Set your OpenAI API key:

```bash
export OPENAI_API_KEY="sk-..."
```

Create `src/mcp_crewai/__init__.py`:

```python
import asyncio
from crewai import Agent, Task, Crew
from langchain_openai import ChatOpenAI

from keycardai.mcp.client import Client
from keycardai.mcp.client.integrations.crewai_agents import CrewAIClient

servers = {
    "my-server": {
        "url": "http://localhost:7878/mcp",
        "transport": "http",
        "auth": {"type": "oauth"}
    }
}

async def run():
    async with Client(servers) as mcp_client:
        # Wrap MCP client for CrewAI
        async with CrewAIClient(mcp_client) as crewai_client:
            # Get tools converted to CrewAI format
            tools = await crewai_client.get_tools()
            print(f"Available tools: {len(tools)}")

            # Get system prompt
            system_prompt = crewai_client.get_system_prompt(
                "You are a helpful assistant with access to MCP tools."
            )

            # Create CrewAI agent
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
            agent = Agent(
                role="MCP Assistant",
                goal=system_prompt,
                backstory="An AI assistant that can use MCP tools to help users.",
                tools=tools,
                llm=llm,
                verbose=True
            )

            # Create task
            task = Task(
                description="List the available tools and use one of them to demonstrate functionality.",
                expected_output="A summary of the tools and the result of using one tool.",
                agent=agent
            )

            # Create and run crew
            crew = Crew(
                agents=[agent],
                tasks=[task],
                verbose=True
            )

            print("\nStarting CrewAI crew...")
            result = crew.kickoff()
            print(f"\nCrew result: {result}")

def main():
    """Entry point for CrewAI agent."""
    asyncio.run(run())
```

Run the CrewAI agent:

```bash
uv run mcp-crewai
```

---

## Troubleshooting

### OAuth Browser Doesn't Open (LocalAuthCoordinator)

- Ensure `webbrowser` module can open URLs
- Try manually opening the URL printed in logs
- Check firewall settings for local callback server

### Tokens Not Persisting (Serverless)

- Verify `SQLiteBackend` path is accessible across invocations
- Use shared storage (S3, DynamoDB) instead of local filesystem
- Check file permissions on SQLite database

### Multi-User Token Leakage

- Always use `ClientManager.get_client(user_id)` for user isolation
- Never share `Client` instances across users
- Verify storage namespaces include user/context ID

### OAuth Callback Not Received

- Verify `redirect_uri` matches OAuth provider configuration
- Check callback endpoint is publicly accessible (for web apps)
- Ensure `handle_completion()` is called with correct parameters

---

## Support

- **Documentation:** [https://docs.keycard.cloud](https://docs.keycard.cloud)
- **GitHub:** [https://github.com/keycardai/python-sdk](https://github.com/keycardai/python-sdk)
- **Issues:** [https://github.com/keycardai/python-sdk/issues](https://github.com/keycardai/python-sdk/issues)

---

## License

Copyright © 2025 Keycard. All rights reserved.

