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

## MCP Client

The Keycard MCP SDK also includes **MCP Client** for connecting to MCP servers with built-in authentication support.

### Key Features

- ✅ **OAuth 2.0 Support**: Automatic OAuth flow handling for protected MCP servers
- ✅ **Graceful Connection Handling**: Non-throwing connection behavior with comprehensive status tracking
- ✅ **Multi-Server Support**: Connect to multiple servers with independent status tracking
- ✅ **AI Agent Integrations**: Pre-built integrations with LangChain and OpenAI Agents SDK

### Quick Start

```python
import asyncio
from keycardai.mcp.client import Client

servers = {
    "my-server": {
        "url": "http://localhost:7878/mcp",
        "transport": "http",
        "auth": {"type": "oauth"}  # OAuth configured via server config
    }
}

async def main():
    async with Client(servers) as client:
        # Connection failures are communicated via session status, not exceptions
        session = client.sessions["my-server"]
        if not session.is_operational:
            print(f"Server not available: {session.status}")
            return
        
        # List available tools
        tools = await client.list_tools()
        
        # Call a tool
        result = await client.call_tool("my-tool", {"arg": "value"})
        print(result)

asyncio.run(main())
```

### Learn More

For comprehensive documentation including use cases, configuration examples, and advanced patterns, see the [MCP Client README](src/keycardai/mcp/client/README.md).

### Delegated Access

Keycard allows MCP servers to access other resources on behalf of users with automatic consent and secure token exchange.

#### Setup Protected Resources

1. **Configure credential provider** (e.g., Google Workspace)
2. **Configure protected resource** (e.g., Google Drive API)  
3. **Set MCP server dependencies** to allow delegated access
4. **Create client secret identity** to provide authentication method

#### Zone Configuration

Keycard zones are isolated environments for authentication and authorization. You can configure zone settings explicitly in code or automatically discover them from environment variables.

##### Configuration Methods

**1. Explicit Configuration (Recommended for Production)**

```python
from keycardai.mcp.server.auth import AuthProvider

# Using zone_id (constructs zone URL automatically)
auth_provider = AuthProvider(
    zone_id="your-zone-id",
    mcp_server_name="My MCP Server"
)

# Using explicit zone_url
auth_provider = AuthProvider(
    zone_url="https://your-zone-id.keycard.cloud",
    mcp_server_name="My MCP Server"
)

# Using custom base_url with zone_id
auth_provider = AuthProvider(
    zone_id="your-zone-id",
    base_url="https://custom.keycard.example.com",
    mcp_server_name="My MCP Server"
)
```

**2. Environment Variable Discovery**

The SDK automatically discovers zone configuration from environment variables:

```bash
# Option 1: Set zone_id (URL will be constructed)
export KEYCARD_ZONE_ID="your-zone-id"

# Option 2: Set explicit zone URL
export KEYCARD_ZONE_URL="https://your-zone-id.keycard.cloud"

# Option 3: Customize base URL for zone construction
export KEYCARD_ZONE_ID="your-zone-id"
export KEYCARD_BASE_URL="https://custom.keycard.example.com"
```

```python
from keycardai.mcp.server.auth import AuthProvider

# Automatically discovers zone configuration from environment
auth_provider = AuthProvider(
    mcp_server_name="My MCP Server"
)
```

##### Configuration Precedence

When multiple zone configuration methods are present, the SDK follows this precedence order (highest to lowest):

1. **Explicit `zone_url` parameter** - Always takes priority
2. **`KEYCARD_ZONE_URL` environment variable** - Direct zone URL
3. **Explicit `zone_id` parameter** - Combined with base_url to construct zone URL
4. **`KEYCARD_ZONE_ID` environment variable** - Combined with base_url to construct zone URL
5. **Error** - At least one zone configuration method is required

For `base_url`, the precedence is:
1. **Explicit `base_url` parameter** - Custom base URL
2. **`KEYCARD_BASE_URL` environment variable** - Custom base URL from environment
3. **Default: `https://keycard.cloud`** - Standard Keycard cloud URL

##### Environment Variables Reference

| Environment Variable | Purpose | Default Value |
|---------------------|---------|---------------|
| `KEYCARD_ZONE_ID` | Zone identifier for constructing zone URL | None (required if zone_url not set) |
| `KEYCARD_ZONE_URL` | Complete zone URL (overrides zone_id) | None |
| `KEYCARD_BASE_URL` | Base URL for zone construction | `https://keycard.cloud` |

#### Application Credentials for Token Exchange

To enable token exchange (required for the `@grant` decorator), you need to configure application credentials. The SDK supports multiple credential types and provides automatic discovery via environment variables.

##### Credential Types

The SDK supports three types of application credentials:

1. **ClientSecret** - OAuth client credentials (client_id/client_secret) issued by Keycard
2. **WebIdentity** - Private key JWT authentication for MCP servers
3. **EKSWorkloadIdentity** - AWS EKS Pod Identity for Kubernetes deployments

##### Configuration Methods

**1. Explicit Configuration (Recommended for Production)**

Explicitly provide credentials when creating the `AuthProvider`:

```python
from keycardai.mcp.server.auth import AuthProvider, ClientSecret

# Client Secret credentials
auth_provider = AuthProvider(
    zone_id="your-zone-id",
    mcp_server_name="My MCP Server",
    application_credential=ClientSecret(("your_client_id", "your_client_secret"))
)
```

```python
from keycardai.mcp.server.auth import AuthProvider, WebIdentity

# Web Identity (Private Key JWT)
auth_provider = AuthProvider(
    zone_id="your-zone-id",
    mcp_server_name="My MCP Server",
    application_credential=WebIdentity(
        mcp_server_name="My MCP Server",
        storage_dir="./mcp_keys"  # Directory for key storage
    )
)
```

```python
from keycardai.mcp.server.auth import AuthProvider, EKSWorkloadIdentity

# EKS Workload Identity
auth_provider = AuthProvider(
    zone_id="your-zone-id",
    mcp_server_name="My MCP Server",
    application_credential=EKSWorkloadIdentity()
)
```

**2. Environment Variable Discovery (Convenient for Development)**

The SDK automatically discovers credentials from environment variables:

```bash
# Option A: Client Credentials
export KEYCARD_CLIENT_ID="your_client_id"
export KEYCARD_CLIENT_SECRET="your_client_secret"

# Option B: Explicit Credential Type
export KEYCARD_APPLICATION_CREDENTIAL_TYPE="web_identity"
export KEYCARD_WEB_IDENTITY_KEY_STORAGE_DIR="./mcp_keys"  # Optional

# Option C: EKS Workload Identity
export KEYCARD_APPLICATION_CREDENTIAL_TYPE="eks_workload_identity"
# Optional: Custom token file path (defaults to AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE or AWS_WEB_IDENTITY_TOKEN_FILE)
export KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE="/var/run/secrets/token"
```

With environment variables configured, create the `AuthProvider` without explicit credentials:

```python
from keycardai.mcp.server.auth import AuthProvider

# Credentials automatically discovered from environment variables
auth_provider = AuthProvider(
    zone_id="your-zone-id",
    mcp_server_name="My MCP Server"
)
```

##### Configuration Precedence

When multiple configuration methods are present, the SDK follows this precedence order (highest to lowest):

1. **Explicit `application_credential` parameter** - Always takes priority
2. **`KEYCARD_CLIENT_ID` + `KEYCARD_CLIENT_SECRET`** - Client credentials via environment
3. **`KEYCARD_APPLICATION_CREDENTIAL_TYPE`** - Explicit credential type selection
4. **`AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE`** - Automatic EKS detection
5. **None** - No credentials configured (token exchange disabled)

##### Environment Variables Reference

| Environment Variable | Purpose | Used By | Default Value |
|---------------------|---------|---------|---------------|
| `KEYCARD_CLIENT_ID` | OAuth client identifier | `ClientSecret` | None |
| `KEYCARD_CLIENT_SECRET` | OAuth client secret | `ClientSecret` | None |
| `KEYCARD_APPLICATION_CREDENTIAL_TYPE` | Explicit credential type selection | All | None |
| `KEYCARD_WEB_IDENTITY_KEY_STORAGE_DIR` | Directory for private key storage | `WebIdentity` | `"./mcp_keys"` |
| `KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE` | Custom path to EKS token file | `EKSWorkloadIdentity` | None |
| `AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE` | Path to EKS token file (AWS default) | `EKSWorkloadIdentity` | None |
| `AWS_WEB_IDENTITY_TOKEN_FILE` | Path to EKS token file (AWS fallback) | `EKSWorkloadIdentity` | None |

##### Running Without Application Credentials

If no application credentials are configured, the `AuthProvider` will work for basic authentication but the `@grant` decorator will be unable to perform token exchange. This is useful for MCP servers that only need user authentication without delegated access to external resources.

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
