# KeyCard AI MCP SDK

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

Add KeyCard authentication to your existing MCP server:

### Install the Package

```bash
pip install keycardai-mcp
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

### 🎉 Your MCP server is now protected with KeyCard authentication! 🎉

## Features

- ✅ **OAuth 2.0 Authentication**: Secure your MCP server with industry-standard OAuth flows
- ✅ **Easy Integration**: Add authentication with just a few lines of code
- ✅ **Multi-Zone Support**: Support multiple KeyCard zones in one application
- ✅ **Token Exchange**: Automatic delegated token exchange for accessing external APIs
- ✅ **Production Ready**: Battle-tested security patterns and error handling

### Delegated Access

KeyCard allows MCP servers to access other resources on behalf of users with automatic consent and secure token exchange.

#### Setup Protected Resources

1. **Configure credential provider** (e.g., Google Workspace)
2. **Configure protected resource** (e.g., Google Drive API)  
3. **Set MCP server dependencies** to allow delegated access
4. **Create client secret identity** to provide authentication method

#### Add Delegation to Your Tools

```python
from mcp.server.fastmcp import FastMCP, Context
from keycardai.mcp.server.auth import AuthProvider, AccessContext, BasicAuth
import os

# Configure your provider
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

## Examples

For complete examples and advanced usage patterns, see our [documentation](https://docs.keycard.ai).

## License

MIT License - see [LICENSE](https://github.com/keycardai/python-sdk/blob/main/LICENSE) file for details.

## Support

- 📖 [Documentation](https://docs.keycard.ai)
- 🐛 [Issue Tracker](https://github.com/keycardai/python-sdk/issues)
- 📧 [Support Email](mailto:support@keycard.ai)
