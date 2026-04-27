# keycardai-fastmcp

FastMCP integration for Keycard OAuth: protect FastMCP servers with Keycard
authentication and run delegated OAuth 2.0 token exchange (RFC 8693) for
downstream APIs.

This is the canonical home for the integration; `keycardai-mcp-fastmcp` is
preserved as a deprecation bridge for callers still on the old name.

## Installation

```bash
pip install keycardai-fastmcp
```

## Quick Start

```python
from fastmcp import FastMCP, Context
from keycardai.fastmcp import AuthProvider

auth_provider = AuthProvider(
    zone_id="abc1234",
    mcp_server_name="My Server",
    mcp_base_url="http://localhost:8000",
)

mcp = FastMCP("My Server", auth=auth_provider.get_remote_auth_provider())

@mcp.tool()
@auth_provider.grant("https://api.example.com")
async def call_external_api(ctx: Context, query: str):
    keycardai = await ctx.get_state("keycardai")
    token = keycardai.access("https://api.example.com").access_token
    return f"Results for {query} (token starts with {token[:8]})"
```

## Migration from `keycardai-mcp-fastmcp`

The old package keeps working: `from keycardai.mcp.integrations.fastmcp import AuthProvider`
emits a `DeprecationWarning` pointing here and returns the same class. Migrate
when convenient.
