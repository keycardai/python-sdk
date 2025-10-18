# Keycard MCP SDK

A comprehensive Python SDK for Model Context Protocol (MCP) functionality that simplifies authentication and authorization concerns for developers working with AI/LLM integrations.

## Requirements

- **Python 3.9 or greater**
- Virtual environment (recommended)

## Setup Guide

### Option 1: Using uv (Recommended)

If you have [uv](https://docs.astral.sh/uv/) installed:

```bash
# Create a new project with uv
uv init my-mcp-project
cd my-mcp-project

# Create and activate virtual environment
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### Option 2: Using Standard Python

```bash
# Create project directory
mkdir my-mcp-project
cd my-mcp-project

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Upgrade pip (recommended)
pip install --upgrade pip
```

## Installation

```bash
pip install keycardai-mcp
```

## Quick Start

Add Keycard authentication to your existing MCP server:

### Install the Package

```bash
pip install keycardai-mcp
```

### Get Your Keycard Zone ID

1. Sign up at [keycard.ai](https://keycard.ai)
2. Navigate to Zone Settings to get your zone ID
3. Configure your preferred identity provider (Google, Microsoft, etc.)
4. Create an MCP resource in your zone

### Add Authentication to Your MCP Server

```python
from mcp.server.fastmcp import FastMCP
from keycardai.mcp.server.auth import AuthProvider

# Your existing MCP server
mcp = FastMCP("My Secure MCP Server")

@mcp.tool()
def my_protected_tool(data: str) -> str:
    return f"Processed: {data}"

# Add Keycard authentication
access = AuthProvider(
    zone_id="your_zone_id_here",
    mcp_server_name="My Secure MCP Server",
)

# Create authenticated app
app = access.app(mcp)
```

### Run with Authentication

```bash
pip install uvicorn
uvicorn server:app
```

### 🎉 Your MCP server is now protected with Keycard authentication! 🎉

## Features

- ✅ **OAuth 2.0 Authentication**: Secure your MCP server with industry-standard OAuth flows
- ✅ **Easy Integration**: Add authentication with just a few lines of code
- ✅ **Multi-Zone Support**: Support multiple Keycard zones in one application
- ✅ **Token Exchange**: Automatic delegated token exchange for accessing external APIs
- ✅ **Production Ready**: Battle-tested security patterns and error handling

### Delegated Access

Keycard allows MCP servers to access other resources on behalf of users with automatic consent and secure token exchange.

#### Setup Protected Resources

1. **Configure credential provider** (e.g., Google Workspace)
2. **Configure protected resource** (e.g., Google Drive API)  
3. **Set MCP server dependencies** to allow delegated access
4. **Create client secret identity** to provide authentication method

#### Add Delegation to Your Tools

```python
from mcp.server.fastmcp import FastMCP, Context
from keycardai.mcp.server.auth import AuthProvider, AccessContext, ClientSecret
import os

# Configure your provider with client credentials
access = AuthProvider(
    zone_id="your_zone_id",
    mcp_server_name="My MCP Server",
    application_credential=ClientSecret((
        os.getenv("KEYCARD_CLIENT_ID"),
        os.getenv("KEYCARD_CLIENT_SECRET")
    ))
)

mcp = FastMCP("My MCP Server")

@mcp.tool()
@access.grant("https://protected-api")
def protected_tool(ctx: Context, access_context: AccessContext, name: str) -> str:
    # Use the access_context to call external APIs on behalf of the user
    token = access_context.access("https://protected-api").access_token
    # Make authenticated API calls...
    return f"Protected data for {name}"

app = access.app(mcp)
```

### Lowlevel Integration

For advanced use cases requiring direct control over the MCP server lifecycle, you can integrate Keycard with the lowlevel MCP server API.

#### Requirements

When using lowlevel integration with Keycard:

1. **Function Parameters**: Functions decorated with `@auth.grant()` must accept `RequestContext` and `AccessContext` parameters:
   ```python
   @auth.grant("https://protected-api")
   def echo_handler(arguments: dict[str, Any], ctx: RequestContext, access_context: AccessContext) -> list[TextContent]:
       # Your implementation
   ```

2. **RequestContext Responsibility**: Unlike FastMCP which automatically injects the context, lowlevel servers require you to manually pass the `RequestContext` from `server.request_context` to your handler functions.
    ```python
    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict[str, Any] | None = None) -> list[TextContent]:
        # Pass server.request_context to the tool call. 
        return await echo_handler(arguments, server.request_context)
    ```

3. **ASGI Integration**: Use `auth.get_mcp_router()` to create authenticated routes that wrap your MCP transport or session manager.

#### Option 1: Using StreamableHTTPServerTransport

```python
import uvicorn
import asyncio
from contextlib import asynccontextmanager
from starlette.applications import Starlette
from starlette.types import Scope, Receive, Send
from mcp.server.lowlevel import Server
from mcp.shared.context import RequestContext
from mcp.types import Tool, TextContent
from mcp.server.streamable_http import StreamableHTTPServerTransport
from typing import Any

from keycardai.mcp.server.auth import AuthProvider, AccessContext

# Configure Keycard authentication
auth = AuthProvider(
    zone_id="your_zone_id",
    mcp_server_name="lowlevel-mcp",
    enable_multi_zone=True,
)

class StreamableHTTPASGIApp:
    def __init__(self, transport: StreamableHTTPServerTransport):
        self.transport = transport

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.transport.handle_request(scope, receive, send)

# Create MCP server and transport
server = Server("lowlevel-mcp")
transport = StreamableHTTPServerTransport()

# Define protected tool with delegated access
@auth.grant("https://protected-api")
def echo_handler(arguments: dict[str, Any], ctx: RequestContext, access_context: AccessContext) -> list[TextContent]:
    if access_context.has_errors():
        return [TextContent(type="text", text=f"Error: {access_context.get_errors()}")]
    
    # Access external API with delegated token
    token = access_context.access("https://protected-api").access_token
    return [TextContent(type="text", text=f"Echo: {arguments['message']}")]

# Register tools
@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [Tool(
        name="echo",
        description="Echo a message with protected access",
        inputSchema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"]
        }
    )]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any] | None = None) -> list[TextContent]:
    if name == "echo":
        # Pass RequestContext from server to decorated handler
        return await echo_handler(arguments, server.request_context)
    raise Exception(f"Unknown tool: {name}")

@asynccontextmanager
async def lifespan(app):
    async with transport.connect() as (read_stream, write_stream):
        server_task = asyncio.create_task(server.run(
            read_stream, write_stream, server.create_initialization_options()
        ))
        try:
            yield
        finally:
            server_task.cancel()

# Create authenticated ASGI app
app = Starlette(
    routes=auth.get_mcp_router(StreamableHTTPASGIApp(transport)),
    lifespan=lifespan,
)
```

#### Option 2: Using StreamableHTTPSessionManager

```python
import uvicorn
import asyncio
from starlette.applications import Starlette
from starlette.types import Scope, Receive, Send
from mcp.server.lowlevel import Server
from mcp.shared.context import RequestContext
from mcp.types import Tool, TextContent
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from typing import Any

from keycardai.mcp.server.auth import AuthProvider, AccessContext

# Configure Keycard authentication
auth = AuthProvider(
    zone_id="your_zone_id",
    mcp_server_name="lowlevel-mcp",
    enable_multi_zone=True,
)

class StreamableHTTPASGIApp:
    def __init__(self, session_manager: StreamableHTTPSessionManager):
        self.session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Send, send: Send) -> None:
        await self.session_manager.handle_request(scope, receive, send)

# Create MCP server and session manager
server = Server("lowlevel-mcp")
session_manager = StreamableHTTPSessionManager(
    app=server,
    stateless=True,
)

# Define protected tool with delegated access
@auth.grant("https://protected-api")
def echo_handler(arguments: dict[str, Any], ctx: RequestContext, access_context: AccessContext) -> list[TextContent]:
    if access_context.has_errors():
        return [TextContent(type="text", text=f"Error: {access_context.get_errors()}")]
    
    # Access external API with delegated token
    token = access_context.access("https://protected-api").access_token
    return [TextContent(type="text", text=f"Echo: {arguments['message']}")]

# Register tools
@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [Tool(
        name="echo",
        description="Echo a message with protected access",
        inputSchema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"]
        }
    )]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any] | None = None) -> list[TextContent]:
    if name == "echo":
        # Pass RequestContext from server to decorated handler
        return await echo_handler(arguments, server.request_context)
    raise Exception(f"Unknown tool: {name}")

# Create authenticated ASGI app
app = Starlette(
    routes=auth.get_mcp_router(StreamableHTTPASGIApp(session_manager)),
    lifespan=lambda app: session_manager.run(),
)

# Run the server
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

Both approaches provide full control over the MCP server lifecycle while maintaining Keycard's authentication and delegated access capabilities.

### Error Handling

The Keycard MCP package implements a robust error handling system that allows functions to continue execution even when delegation processes fail. This is achieved through the `AccessContext` object, which manages both successful tokens and error states without raising exceptions.

#### How AccessContext Manages Errors

The `AccessContext` serves as a centralized error management system during the OAuth token delegation process:

**Error Types:**
- **Global Errors**: Affect all resources (e.g., missing authentication, configuration issues)
- **Resource-Specific Errors**: Affect individual resources during token exchange

The `@grant` decorator automatically handles all error scenarios and populates the `AccessContext` with appropriate error information, ensuring your functions can always execute and handle errors gracefully.

#### Error Scenarios

The `@grant` decorator handles multiple error scenarios automatically:

- **Authentication Errors**: Missing or invalid authentication tokens
- **Configuration Errors**: Server misconfiguration or missing zone information  
- **Token Exchange Errors**: Failures when exchanging tokens for specific resources

All errors include descriptive messages to help with debugging and user-friendly error handling.

#### Usage Patterns

**Basic Error Checking:**
```python
@provider.grant("https://api.example.com")
def my_tool(access_ctx: AccessContext, ctx: Context, user_id: str):
    # Check for any errors first
    if access_ctx.has_errors():
        error_info = access_ctx.get_errors()
        return {"error": "Token delegation failed", "details": error_info}
    
    # Proceed with successful token
    token = access_ctx.access("https://api.example.com").access_token
    return call_external_api(token, user_id)
```

**Partial Success Handling:**
```python
@provider.grant(["https://api1.com", "https://api2.com"])
def multi_resource_tool(access_ctx: AccessContext, ctx: Context):
    results = {}
    
    # Handle successful resources
    for resource in access_ctx.get_successful_resources():
        token = access_ctx.access(resource).access_token
        results[resource] = call_api(resource, token)
    
    # Handle failed resources
    for resource in access_ctx.get_failed_resources():
        error = access_ctx.get_resource_errors(resource)
        results[resource] = {"error": error["error"]}
    
    return results
```

**Status-Based Handling:**
```python
@provider.grant("https://api.example.com")
def status_aware_tool(access_ctx: AccessContext, ctx: Context):
    status = access_ctx.get_status()  # "success", "partial_error", or "error"
    
    if status == "error":
        return {"status": "failed", "reason": access_ctx.get_error()}
    elif status == "partial_error":
        return {"status": "partial", "details": access_ctx.get_errors()}
    else:
        token = access_ctx.access("https://api.example.com").access_token
        return {"status": "success", "data": call_api(token)}
```

## FAQ

### How to test the MCP server with modelcontexprotocol/inspector?

When testing your MCP server with the [modelcontexprotocol/inspector](https://github.com/modelcontextprotocol/inspector), you may need to configure CORS (Cross-Origin Resource Sharing) to allow the inspector's web interface to access your protected endpoints from localhost.

You can use Starlette's built-in `CORSMiddleware` to configure CORS settings:

```python
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Allow all origins for testing
        allow_credentials=True,
        allow_methods=["*"],  # Allow all HTTP methods
        allow_headers=["*"],  # Allow all headers
    )
]

app = access.app(mcp, middleware=middleware)
```

**Important Security Note:** The configuration above uses permissive CORS settings (`allow_origins=["*"]`) which should **only be used for local development and testing**. In production environments, you should restrict `allow_origins` to specific domains that need access to your MCP server.

For production use, consider more restrictive settings:

```python
middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=["https://yourdomain.com"],  # Specific allowed origins
        allow_credentials=True,
        allow_methods=["GET", "POST"],  # Only required methods
        allow_headers=["Authorization", "Content-Type"],  # Only required headers
    )
]
```

## Examples

For complete examples and advanced usage patterns, see our [documentation](https://docs.keycard.ai).

## License

MIT License - see [LICENSE](https://github.com/keycardai/python-sdk/blob/main/LICENSE) file for details.

## Support

- 📖 [Documentation](https://docs.keycard.ai)
- 🐛 [Issue Tracker](https://github.com/keycardai/python-sdk/issues)
- 📧 [Support Email](mailto:support@keycard.ai)
