# KeyCard Python SDK

A collection of Python packages for KeyCard services, organized as a uv workspace.

## Quick Start

Get up and running with KeyCard's MCP (Model Context Protocol) integration in minutes:

### Install the Packages

```bash
pip install mcp keycardai-mcp
```

or 

```bash
uv add mcp keycardai-mcp
```

### Create Your First MCP Server

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Hello World")

@mcp.tool()
def hello_world(name: str) -> str:
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

### Run your MCP server

```bash
python server.py
```

For more detail refer to the [mcp](https://github.com/modelcontextprotocol/python-sdk?tab=readme-ov-file#streamable-http-transport) documentation

### Configure the remote MCP in your AI client, like [Cursor](https://cursor.com/?from=home)

```json
{
  "mcpServers": {
    "hello-world": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

### Test the remote server with client, 

<img src="docs/images/cursor_hello_world_agent_call.png" alt="Cursor Hello World Agent Call" width="500">

### Signup to Keycard and get your zone identifier

Refer to [docs](https://docs.keycard.ai/) on how to signup. Navigate to Zone Settings to obtain the zone id

<img src="docs/images/keycard_zone_information.png" alt="Keycard ZoneId Information" width="400">

### Configure Your prefered Identity Provider

<img src="docs/images/keycard_identity_provider_config.png" alt="Keycard Identity Provider Configuration" width="400">

### Setup MCP resource

<img src="docs/images/create_mcp_resource.png" alt="Create MCP Resource" width="400">

### Add authentication to the MCP server

```python
from mcp.server.fastmcp import FastMCP

from keycardai.mcp.server.auth import AuthProvider

# From the zone setting above
zone_id = "90zqtq5lvtobrmyl3b0i0k2z1q"

access = AuthProvider(
   zone_id = zone_id,
   mcp_server_name="Hello World Mcp",
)

mcp = FastMCP("Minimal MCP")

@mcp.tool()
def hello_world(name: str) -> str:
    return f"Hello, {name}!"

# Create starlett app to handle authorization flows
app = access.app(mcp)
```

### Run Your Server

The authorization flows require additonal handlers to advertise the metadata.

This is implemented using underlying starlett application, for more information refer to official [mcp](https://github.com/modelcontextprotocol/python-sdk?tab=readme-ov-file#streamablehttp-servers) documentation

You can use any async server, for example [uvicorn](https://www.uvicorn.org/)

```bash
uv add uvicorn
```

or

```bash
pip install uvicorn
```

```bash
uvicorn server:app
```

### Authenticate in client

<img src="docs/images/cursor_authenticate.png" alt="Cursor Authentication Prompt" width="500">


### 🎉 Your MCP server is now running with KeyCard authentication! 🎉


## Overview

This workspace contains multiple Python packages that provide various KeyCard functionality:

- **keycardai-oauth**: OAuth 2.0 implementation with support for RFC 8693 (Token Exchange)
- **keycardai-mcp**: Core MCP (Model Context Protocol) integration utilities
- **keycardai-mcp-fastmcp**: FastMCP-specific integration package with decorators and middleware

## Installation

Install the SDK packages using pip:

```bash
# Install individual packages as needed
pip install keycardai-oauth
pip install keycardai-mcp
pip install keycardai-mcp-fastmcp

# Or install from source
git clone git@github.com:keycardai/python-sdk.git
cd python-sdk
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