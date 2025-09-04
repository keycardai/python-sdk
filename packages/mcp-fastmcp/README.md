# KeyCard AI FastMCP Integration

A Python package that provides seamless integration between KeyCard's OAuth client and FastMCP servers, enabling secure token exchange and authentication for MCP tools.

## Installation

```bash
pip install keycardai-mcp-fastmcp
```

## Quick Start

```python
from fastmcp import FastMCP, Context
from keycardai.mcp.integrations.fastmcp import KeycardAuthProvider, OAuthClientMiddleware, keycardai

# Create FastMCP server with KeyCard authentication
mcp = FastMCP("My Secure Service")

# Add KeyCard authentication
auth = KeycardAuthProvider(
    zone_url="https://abc1234.keycard.cloud",
    mcp_server_name="My MCP Service",
    required_scopes=["calendar:read"]
)
mcp.set_auth_provider(auth)

# Add OAuth client middleware for token exchange
oauth_middleware = OAuthClientMiddleware(
    base_url="https://abc1234.keycard.cloud",
    client_name="My MCP Service"
)
mcp.add_middleware(oauth_middleware)

# Use decorator for automatic token exchange
@mcp.tool()
@keycardai.request_access_for_resource("https://www.googleapis.com/calendar/v3")
async def get_calendar_events(ctx: Context, maxResults: int = 10) -> dict:
    # ctx.access_token is automatically available with Google Calendar access
    access_token = ctx.access_token
    # Make API calls with the exchanged token...
    return {"events": [...], "totalEvents": 5}
```

## 🏗️ Architecture & Features

This integration package provides FastMCP-specific components for KeyCard OAuth:

### Core Components

| Component | Module | Description |
|-----------|---------|-------------|
| **KeycardAuthProvider** | `provider.py` | **FastMCP Authentication** - Integrates KeyCard zone tokens with FastMCP auth system |
| **OAuthClientMiddleware** | `middleware.py` | **Client Lifecycle** - Manages OAuth client initialization and context injection |
| **Token Exchange Decorators** | `decorators.py` | **Automated Exchange** - Decorators for seamless resource-specific token exchange |

### Authentication Flow

1. **Token Verification**: `KeycardAuthProvider` validates incoming JWT tokens using KeyCard zone JWKS
2. **Client Management**: `OAuthClientMiddleware` provides OAuth client to tools via FastMCP context
3. **Token Exchange**: `@keycardai.request_access_for_resource()` decorator automates RFC 8693 token exchange
4. **API Access**: Tools receive resource-specific access tokens transparently

### Key Features

| Feature | Implementation | Description |
|---------|---------|-------------|
| **Automatic Discovery** | `KeycardTokenVerifier` | **Endpoint Discovery** - Automatically discovers JWKS URI and issuer from KeyCard zone |
| **JWT Verification** | FastMCP `JWTVerifier` | **Token Validation** - Leverages FastMCP's robust JWT verification |
| **Resource Exchange** | `request_access_for_resource` | **Delegated Access** - Seamless token exchange for specific APIs |
| **Context Integration** | `OAuthClientMiddleware` | **State Management** - OAuth client available via FastMCP context |

## Features

- ✅ **FastMCP Integration**: Native integration with FastMCP server framework
- ✅ **KeyCard Zone Support**: Automatic discovery and integration with KeyCard zones
- ✅ **Token Exchange**: RFC 8693 compliant delegated token exchange for external APIs
- ✅ **Decorator Pattern**: Simple `@keycardai.request_access_for_resource()` decorator
- ✅ **Context Injection**: OAuth client available via FastMCP context system
- ✅ **Type Safety**: Full type hints with Pydantic validation
- ✅ **Async Support**: Native async/await for all operations
- ✅ **Production Ready**: Robust error handling and resource management

## Use Cases

### 🗓️ Google Calendar Integration
```python
from keycardai.mcp.integrations.fastmcp import keycardai

@mcp.tool()
@keycardai.request_access_for_resource("https://www.googleapis.com/calendar/v3")
async def get_calendar_events(ctx: Context, maxResults: int = 10) -> dict:
    # Token exchange happens automatically
    access_token = ctx.access_token
    
    # Make Google Calendar API calls
    events = await fetch_google_calendar_events(
        access_token=access_token,
        max_results=maxResults
    )
    return {"events": events}
```

### 🔐 Multi-API Access
```python
# Different tools can access different APIs seamlessly
@mcp.tool()
@keycardai.request_access_for_resource("https://graph.microsoft.com")
async def get_outlook_events(ctx: Context) -> dict:
    # Automatic token exchange for Microsoft Graph
    return await fetch_outlook_events(ctx.access_token)

@mcp.tool()
@keycardai.request_access_for_resource("https://www.googleapis.com/drive/v3")
async def list_drive_files(ctx: Context) -> dict:
    # Automatic token exchange for Google Drive
    return await fetch_drive_files(ctx.access_token)
```

### 🏢 Enterprise FastMCP Server
```python
# Production FastMCP server with KeyCard authentication
auth = KeycardAuthProvider(
    zone="https://company.keycard.cloud",
    service_name="Corporate MCP Service",
    required_scopes=["calendar:read", "drive:read"]
)

mcp = FastMCP("Corporate Assistant", auth=auth)
oauth_middleware = OAuthClientMiddleware(
    base_url="https://company.keycard.cloud",
    client_name="Corporate Assistant"
)
mcp.add_middleware(oauth_middleware)
```

## Security Features

### Token Management
- JWT token verification using KeyCard zone JWKS
- Automatic endpoint discovery from OAuth server metadata
- Secure token exchange following RFC 8693
- Resource-specific token scoping

### Authentication Integration
- FastMCP `JWTVerifier` for robust token validation
- Scope-based authorization enforcement
- Automatic client registration and metadata discovery
- Proper async resource cleanup

### Best Practices
- Context-based token injection (no global state)
- Comprehensive error handling and logging
- Type-safe API design with Pydantic models
- Production-ready middleware patterns

## Development

This package is part of the [KeycardAI Python SDK workspace](../../README.md). 

To develop:

```bash
# From workspace root
uv sync
uv run --package keycardai-mcp-fastmcp pytest
```

## Examples

See the [examples directory](examples/) for comprehensive examples including:
- [Delegated Token Exchange](examples/delegated_token_exchange/) - Google Calendar MCP server
- FastMCP authentication setup
- Token exchange decorator usage
- Production deployment configuration

## License

MIT License - see [LICENSE](../../LICENSE) file for details.

## Support

- 📖 [Documentation](https://docs.keycardai.com/mcp-fastmcp)
- 🐛 [Issue Tracker](https://github.com/keycardai/python-sdk/issues)
- 💬 [Community Discussions](https://github.com/keycardai/python-sdk/discussions)
- 📧 [Support Email](mailto:support@keycard.ai)
