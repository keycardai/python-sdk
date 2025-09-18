# KeyCard AI MCP SDK

A comprehensive Python SDK for Model Context Protocol (MCP) functionality that simplifies authentication and authorization concerns for developers working with AI/LLM integrations.

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
3. Configure your preferred identity provider
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

## Examples

For complete examples and advanced usage patterns, see our [documentation](https://docs.keycard.ai).

## License

MIT License - see [LICENSE](../../LICENSE) file for details.

## Support

- 📖 [Documentation](https://docs.keycard.ai)
- 🐛 [Issue Tracker](https://github.com/keycardai/python-sdk/issues)
- 📧 [Support Email](mailto:support@keycard.ai)
