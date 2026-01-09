# Keycard Python SDK

A collection of Python packages for Keycard services, organized as a uv workspace.

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
    application_credential=ClientSecret((
        os.getenv("KEYCARD_CLIENT_ID"),
        os.getenv("KEYCARD_CLIENT_SECRET")
    ))
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
from keycardai.mcp.server.auth import AuthProvider, AccessContext, ClientSecret

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

### Get Your Keycard Zone ID

1. Sign up at [keycard.ai](https://keycard.ai)
2. Navigate to Zone Settings to get your zone ID
3. Configure your preferred identity provider (Google, Microsoft, etc.)
4. Create an MCP resource in your zone

### Add Authentication to Your FastMCP Server

```python
from fastmcp import FastMCP, Context
from keycardai.mcp.integrations.fastmcp import AuthProvider

# Configure Keycard authentication
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
import os
from fastmcp import FastMCP, Context
from keycardai.mcp.integrations.fastmcp import (
    AuthProvider,
    AccessContext,
    ClientSecret
)

# Configure Keycard authentication with client credentials for delegated access
auth_provider = AuthProvider(
    zone_id="your-zone-id",
    mcp_server_name="My Secure FastMCP Server",
    mcp_base_url="http://127.0.0.1:8000/",
    application_credential=ClientSecret((
        os.getenv("KEYCARD_CLIENT_ID"),
        os.getenv("KEYCARD_CLIENT_SECRET")
    ))
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

### 🎉 Your MCP server is now protected with Keycard authentication! 🎉

## Using FastAPI

Mounting a FastMCP server into a larger FastAPI service introduces a few
gotchas, particularly related to the various OAuth metadata endpoints.

### Standards Compliant Approach

> [!NOTE]
> Most MCP clients expect standards-compliance. Follow this approach if you're
> using those clients or the official MCP SDKs.

The OAuth spec declares that your metadata must be exposed at the root of your
service.

```
/.well-known/oauth-protected-resource
```

This causes a problem when you're mounting multiple APIs or MCP servers to a
common FastAPI service. Each API or MCP Server will potentially have their own
OAuth metadata.

The OAuth spec defines that the metadata for each individual service should be
exposed as an extension to the base `well-known` URI. For example:

```
/.well-known/oauth-protected-resource/api
/.well-known/oauth-protected-resource/mcp-server/mcp
```

To ensure FastMCP and FastAPI produce this, you need to ensure your routing is
defined in a specific way:

```python
from fastmcp import FastMCP
from fastapi import FastAPI

mcp = FastMCP("MCP Server")
mcp_app = mcp.http_app() # DO NOT specify a path here

app = FastAPI(title="API", lifespan=mcp_app.lifespan)

# You MUST mount the MCP's `http_app` to the full path for FastMCP to expose the
# OAuth metadata correctly.
app.mount("/mcp-server/mcp", mcp_app)
```

### Custom, Non Standards Compliant, Approach

> ![WARNING]
> **This is not advised.** Only follow this if you know for sure you need
> flexibility outside of what the spec requires.

If you've built custom clients or need to mount the metadata at a different, non
standards compliant, location, you can do that manually.

#### Mounting at a Custom Root

```python
from fastmcp import FastMCP
from fastapi import FastAPI
from keycardai.mcp.server.routers.metadata import well_known_metadata_mount

auth_provider = AuthProvider(
    zone_id="your-zone-id",  # Get this from keycard.ai
    mcp_server_name="My Secure FastMCP Server",
    mcp_base_url="http://127.0.0.1:8000/"
)

auth = auth_provider.get_remote_auth_provider()

mcp = FastMCP("MCP Server", auth=auth)
mcp_app = mcp.http_app()

app = FastAPI(title="API", lifespan=mcp_app.lifespan)

app.mount(
    "/custom-well-known",
    well_known_metadata_mount(issuer=auth.zone_url),
)
```

which will produce the following endpoints

```
/custom-well-known/oauth-protected-resource
/custom-well-known/oauth-authorization-server
```

#### Mounting at a Specific URI

If you need even more control, you can mount the individual routes at a specific
URI.

```python
from fastmcp import FastMCP
from fastapi import FastAPI
from keycardai.mcp.server.routers.metadata import (
    well_known_authorization_server_route,
    well_known_protected_resource_route,
)

auth_provider = AuthProvider(
    zone_id="your-zone-id",  # Get this from keycard.ai
    mcp_server_name="My Secure FastMCP Server",
    mcp_base_url="http://127.0.0.1:8000/"
)

auth = auth_provider.get_remote_auth_provider()

mcp = FastMCP("MCP Server", auth=auth)
mcp_app = mcp.http_app()

app = FastAPI(title="API", lifespan=mcp_app.lifespan)

app.router.routes.append(
    well_known_protected_resource_route(
        path="/my/custom/path/to/well-known/oauth-protected-resource",
        issuer=auth.zone_url,
    )
)

app.router.routes.append(
    well_known_authorization_server_route(
        path="/my/custom/path/to/well-known/oauth-authorization-server",
        issuer=auth.zone_url,
    )
)
```

which will produce the following endpoints

```
/my/custom/path/to/well-known/oauth-protected-resource
/my/custom/path/to/well-known/oauth-authorization-server
```

## Features

### Delegated Access

Keycard allows MCP servers to access other resources on behalf of users with automatic consent and secure token exchange.

#### Setup Protected Resources

1. **Configure credential provider** (e.g., Google Workspace)
2. **Configure protected resource** (e.g., Google Drive API)
3. **Set MCP server dependencies** to allow delegated access
4. **Create client secret identity** for secure authentication

## Overview

This workspace contains multiple Python packages that provide various Keycard functionality:

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

## FAQ

### How to test the MCP server with modelcontexprotocol/inspector?

When testing your MCP server with the [modelcontexprotocol/inspector](https://github.com/modelcontextprotocol/inspector), you may need to configure CORS (Cross-Origin Resource Sharing) to allow the inspector's web interface to access your protected endpoints from localhost.

**Note:** This applies specifically to the `keycardai-mcp` package. When using `keycardai-mcp-fastmcp`, no middleware is currently required as FastMCP permits access to metadata endpoints by default.

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

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For questions, issues, or support:

- GitHub Issues: [https://github.com/keycardai/python-sdk/issues](https://github.com/keycardai/python-sdk/issues)
- Documentation: [https://docs.keycardai.com](https://docs.keycard.ai/)
- Email: support@keycard.ai
