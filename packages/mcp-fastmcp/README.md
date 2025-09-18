# KeyCard AI FastMCP Integration

A Python package that provides seamless integration between KeyCard and FastMCP servers, enabling secure token exchange and authentication for MCP tools.

## Installation

```bash
pip install keycardai-mcp-fastmcp
```

## Quick Start

Add KeyCard authentication to your existing FastMCP server:

### Install the Package

```bash
pip install keycardai-mcp-fastmcp
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

# Configure KeyCard authentication (recommended: use zone_id)
auth_provider = AuthProvider(
    zone_id="your-zone-id",  # Get this from keycard.ai
    mcp_server_name="My Secure FastMCP Server",
    mcp_server_url="http://127.0.0.1:8000/"
)

# Get the RemoteAuthProvider for FastMCP
auth = auth_provider.get_remote_auth_provider()

# Create authenticated FastMCP server
mcp = FastMCP("My Secure FastMCP Server", auth=auth)

@mcp.tool()
def hello_world(name: str) -> str:
    return f"Hello, {name}!"

# Example with token exchange for external API access
@mcp.tool()
@auth_provider.grant("https://api.example.com")
def call_external_api(ctx: Context, query: str) -> str:
    # Access delegated token through context namespace
    token = ctx.get_state("keycardai").access("https://api.example.com").access_token
    # Use token to call external API
    return f"Results for {query}"

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

### 🎉 Your FastMCP server is now protected with KeyCard authentication! 🎉

## Examples

For complete examples and advanced usage patterns, see our [documentation](https://docs.keycard.ai).

## License

MIT License - see [LICENSE](https://github.com/keycardai/python-sdk/blob/main/LICENSE) file for details.

## Support

- 📖 [Documentation](https://docs.keycard.ai)
- 🐛 [Issue Tracker](https://github.com/keycardai/python-sdk/issues)
- 📧 [Support Email](mailto:support@keycard.ai)
