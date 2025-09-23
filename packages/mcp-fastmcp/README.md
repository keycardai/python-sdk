# KeyCard AI FastMCP Integration

A Python package that provides seamless integration between KeyCard and FastMCP servers, enabling secure token exchange and authentication for MCP tools.

## Requirements

- **Python 3.9 or greater**
- Virtual environment (recommended)

## Setup Guide

### Option 1: Using uv (Recommended)

If you have [uv](https://docs.astral.sh/uv/) installed:

```bash
# Create a new project with uv
uv init my-fastmcp-project
cd my-fastmcp-project

# Create and activate virtual environment
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### Option 2: Using Standard Python

```bash
# Create project directory
mkdir my-fastmcp-project
cd my-fastmcp-project

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Upgrade pip (recommended)
pip install --upgrade pip
```

## Installation

```bash
uv add keycardai-mcp-fastmcp
```

or

```bash
pip install keycardai-mcp-fastmcp
```

## Quick Start

Add KeyCard authentication to your existing FastMCP server:

### Install the Package

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

# Configure KeyCard authentication (recommended: use zone_id)
auth_provider = AuthProvider(
    zone_id="your-zone-id",  # Get this from keycard.ai
    mcp_server_name="My Secure FastMCP Server",
    mcp_base_url="http://127.0.0.1:8000/"  # Note: trailing slash will be added automatically
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

### Add access delegation to tool calls

```python
from fastmcp import FastMCP, Context
from keycardai.mcp.integrations.fastmcp import AuthProvider, AccessContext

# Configure KeyCard authentication (recommended: use zone_id)
auth_provider = AuthProvider(
    zone_id="your-zone-id",  # Get this from keycard.ai
    mcp_server_name="My Secure FastMCP Server",
    mcp_base_url="http://127.0.0.1:8000/"  # Note: trailing slash will be added automatically
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

### 🎉 Your FastMCP server is now protected with KeyCard authentication! 🎉

## Working with AccessContext

When using the `@grant()` decorator, tokens are made available through the `AccessContext` object. This object provides robust error handling and status checking for token exchange operations.

The `@grant()` decorator avoids raising exceptions. Instead, it exposes error information via associated metadata. 
You can check if the context encountered errors by calling the `has_errors()` method.

### Basic Usage

```python
from keycardai.mcp.integrations.fastmcp import AccessContext

@mcp.tool()
@auth_provider.grant("https://api.example.com")
def my_tool(ctx: Context, user_id: str) -> str:
    # Get the access context
    access_context: AccessContext = ctx.get_state("keycardai")
    
    # Always check for errors first
    if access_context.has_errors():
        # Handle the error case
        errors = access_context.get_errors()
        return f"Authentication failed: {errors}"
    
    # Access the token for the specific resource
    token = access_context.access("https://api.example.com").access_token
    
    # Use the token in your API calls
    headers = {"Authorization": f"Bearer {token}"}
    # Make your API request...
    return f"Success for user {user_id}"
```

### Multiple Resources

You can request tokens for multiple resources in a single decorator:

```python
@mcp.tool()
@auth_provider.grant(["https://api.example.com", "https://other-api.com"])
def multi_resource_tool(ctx: Context) -> str:
    access_context: AccessContext = ctx.get_state("keycardai")
    
    # Check overall status
    status = access_context.get_status()  # "success", "partial_error", or "error"
    
    if status == "error":
        # Global error - no tokens available
        return f"Global error: {access_context.get_error()}"
    
    elif status == "partial_error":
        # Some resources succeeded, others failed
        successful = access_context.get_successful_resources()
        failed = access_context.get_failed_resources()
        
        # Work with successful resources only
        for resource in successful:
            token = access_context.access(resource).access_token
            # Use token...
        
        return f"Partial success: {len(successful)} succeeded, {len(failed)} failed"
    
    else:  # status == "success"
        # All resources succeeded
        token1 = access_context.access("https://api.example.com").access_token
        token2 = access_context.access("https://other-api.com").access_token
        # Use both tokens...
        return "All resources accessed successfully"
```

### Error Handling Methods

The `AccessContext` provides several methods for checking errors:

```python
# Check if there are any errors (global or resource-specific)
if access_context.has_errors():
    # Handle any error case

# Check for global errors only
if access_context.has_error():
    global_error = access_context.get_error()

# Check for specific resource errors
if access_context.has_resource_error("https://api.example.com"):
    resource_error = access_context.get_resource_errors("https://api.example.com")

# Get all errors (global + resource-specific)
all_errors = access_context.get_errors()

# Get status summary
status = access_context.get_status()  # "success", "partial_error", or "error"

# Get lists of successful/failed resources
successful_resources = access_context.get_successful_resources()
failed_resources = access_context.get_failed_resources()
```

## Important Configuration Notes

### URL Slash Requirement

⚠️ **Important**: The `mcp_base_url` parameter will automatically have a trailing slash (`/`) appended if not present. This is required for proper JWT audience validation with FastMCP.

**When configuring your KeyCard Resource**, ensure the resource URL in your KeyCard zone settings matches exactly, including the trailing slash:

```python
# This configuration...
auth_provider = AuthProvider(
    zone_id="your-zone-id",
    mcp_base_url="http://localhost:8000"  # No trailing slash
)

# Will become "http://localhost:8000/" internally
# So your KeyCard Resource must be configured as: http://localhost:8000/
```

### Client Credentials for Token Exchange

To enable token exchange (required for the `@grant` decorator), provide client credentials:

```python
from keycardai.oauth.http.auth import BasicAuth

auth_provider = AuthProvider(
    zone_id="your-zone-id",
    mcp_server_name="My FastMCP Service",
    mcp_base_url="http://localhost:8000/",
    auth=BasicAuth("your_client_id", "your_client_secret")
)
```

## Examples

For complete examples and advanced usage patterns, see our [documentation](https://docs.keycard.ai).

## License

MIT License - see [LICENSE](https://github.com/keycardai/python-sdk/blob/main/LICENSE) file for details.

## Support

- 📖 [Documentation](https://docs.keycard.ai)
- 🐛 [Issue Tracker](https://github.com/keycardai/python-sdk/issues)
- 📧 [Support Email](mailto:support@keycard.ai)
