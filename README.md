# KeyCard Python SDK

A collection of Python packages for KeyCard services, organized as a uv workspace.

## Quick Start

Choose the integration that best fits your MCP setup:

## Quick Start with keycardai-mcp (Standard MCP)

For standard MCP servers using the official MCP Python SDK:

### Install the Package

```bash
pip install keycardai-mcp
```

or

```bash
uv add keycardai-mcp
```

### Get Your KeyCard Zone ID

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

# Add KeyCard authentication
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

### Add Delegated Access (Optional)

```python
import os
from mcp.server.fastmcp import FastMCP, Context
from keycardai.mcp.server.auth import AuthProvider, AccessContext, BasicAuth

# Configure your provider with client credentials
access = AuthProvider(
    zone_id="your_zone_id",
    mcp_server_name="My MCP Server",
    auth=BasicAuth(
        os.getenv("KEYCARD_CLIENT_ID"),
        os.getenv("KEYCARD_CLIENT_SECRET")
    )
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

## Quick Start with keycardai-mcp-fastmcp (FastMCP)

For FastMCP servers using the FastMCP framework:

### Install the Package

```bash
pip install keycardai-mcp-fastmcp
```

or

```bash
uv add keycardai-mcp-fastmcp
```

### Get Your KeyCard Zone ID

1. Sign up at [keycard.ai](https://keycard.ai)
2. Navigate to Zone Settings to get your zone ID
3. Configure your preferred identity provider (Google, Microsoft, etc.)
4. Create an MCP resource in your zone

### Add Authentication to Your FastMCP Server

```python
from fastmcp import FastMCP, Context
from keycardai.mcp.integrations.fastmcp import AuthProvider

# Configure KeyCard authentication
auth_provider = AuthProvider(
    zone_id="your-zone-id",  # Get this from keycard.ai
    mcp_server_name="My Secure FastMCP Server",
    mcp_base_url="http://127.0.0.1:8000/"
)

# Get the RemoteAuthProvider for FastMCP
auth = auth_provider.get_remote_auth_provider()

# Create authenticated FastMCP server
mcp = FastMCP("My Secure FastMCP Server", auth=auth)

@mcp.tool()
def hello_world(name: str) -> str:
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

### Add Delegated Access (Optional)

```python
from fastmcp import FastMCP, Context
from keycardai.mcp.integrations.fastmcp import AuthProvider, AccessContext

# Configure KeyCard authentication
auth_provider = AuthProvider(
    zone_id="your-zone-id",
    mcp_server_name="My Secure FastMCP Server",
    mcp_base_url="http://127.0.0.1:8000/"
)

# Get the RemoteAuthProvider for FastMCP
auth = auth_provider.get_remote_auth_provider()

# Create authenticated FastMCP server
mcp = FastMCP("My Secure FastMCP Server", auth=auth)

# Example with token exchange for external API access
@mcp.tool()
@auth_provider.grant("https://api.example.com")
def call_external_api(ctx: Context, query: str) -> str:
    # Get access context to check token exchange status
    access_context: AccessContext = ctx.get_state("keycardai")
    
    # Check for errors before accessing token
    if access_context.has_errors():
        return f"Error: Failed to obtain access token - {access_context.get_errors()}"
    
    # Access delegated token through context namespace
    token = access_context.access("https://api.example.com").access_token
    # Use token to call external API
    return f"Results for {query}"

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

### Configure Your AI Client

Configure the remote MCP in your AI client, like [Cursor](https://cursor.com/?from=home):

```json
{
  "mcpServers": {
    "my-secure-server": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

### 🎉 Your MCP server is now protected with KeyCard authentication! 🎉

## Features

### Delegated Access

KeyCard allows MCP servers to access other resources on behalf of users with automatic consent and secure token exchange.

#### Setup Protected Resources

1. **Configure credential provider** (e.g., Google Workspace)
2. **Configure protected resource** (e.g., Google Drive API)  
3. **Set MCP server dependencies** to allow delegated access
4. **Create client secret identity** for secure authentication


## Overview

This workspace contains multiple Python packages that provide various KeyCard functionality:

- **keycardai-oauth**: OAuth 2.0 implementation with support for RFC 8693 (Token Exchange)
- **keycardai-mcp**: Core MCP (Model Context Protocol) integration utilities for standard MCP servers
- **keycardai-mcp-fastmcp**: FastMCP-specific integration package with decorators and middleware

## Installation

### For Standard MCP Servers

If you're using the official MCP Python SDK:

```bash
pip install keycardai-mcp
```

or

```bash
uv add keycardai-mcp
```

### For FastMCP Servers

If you're using the FastMCP framework:

```bash
pip install keycardai-mcp-fastmcp
```

or

```bash
uv add keycardai-mcp-fastmcp
```

### For OAuth Functionality Only

If you only need OAuth capabilities:

```bash
pip install keycardai-oauth
```

### Install from Source

```bash
git clone git@github.com:keycardai/python-sdk.git
cd python-sdk

# Install specific packages as needed
pip install ./packages/oauth
pip install ./packages/mcp
pip install ./packages/mcp-fastmcp
```

## Documentation

Comprehensive documentation is available at our [documentation site](https://docs.keycard.ai), including:
- API reference for all packages
- Usage examples and tutorials
- Integration guides
- Architecture decisions

## Examples

Each package includes practical examples in their respective `examples/` directories:

- **OAuth examples**: Anonymous token exchange, server discovery, dynamic registration
- **MCP examples**: Google API integration with delegated token exchange

For detailed examples and usage patterns, see our [documentation](https://docs.keycard.ai).

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For questions, issues, or support:

- GitHub Issues: [https://github.com/keycardai/python-sdk/issues](https://github.com/keycardai/python-sdk/issues)
- Documentation: [https://docs.keycardai.com](https://docs.keycard.ai/)
- Email: support@keycard.ai