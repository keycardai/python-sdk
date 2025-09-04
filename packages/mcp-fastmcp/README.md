# KeyCard AI FastMCP Integration

A Python package that provides seamless integration between KeyCard and FastMCP servers, enabling secure token exchange and authentication for MCP tools.

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
    mcp_server_name="My MCP Service"
)
mcp.set_auth_provider(auth)

# Add OAuth client middleware for token exchange
oauth_middleware = OAuthClientMiddleware(
    zone_url="https://abc1234.keycard.cloud",
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

## Development

This package is part of the [KeycardAI Python SDK](../../README.md). 

To develop:

```bash
# From workspace root
uv sync
uv run --package keycardai-mcp-fastmcp pytest
```
## License

MIT License - see [LICENSE](../../LICENSE) file for details.

## Support

- 🐛 [Issue Tracker](https://github.com/keycardai/python-sdk/issues)
- 💬 [Community Discussions](https://github.com/keycardai/python-sdk/discussions)
- 📧 [Support Email](mailto:support@keycard.ai)
