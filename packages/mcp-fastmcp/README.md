# Keycard FastMCP Integration

A Python package that provides seamless integration between Keycard and FastMCP servers, enabling secure token exchange and authentication for MCP tools.

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

Add Keycard authentication to your existing FastMCP server:

### Install the Package

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

# Configure Keycard authentication (recommended: use zone_id)
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

# Configure Keycard authentication (recommended: use zone_id)
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

### 🎉 Your FastMCP server is now protected with Keycard authentication! 🎉

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

**When configuring your Keycard Resource**, ensure the resource URL in your Keycard zone settings matches exactly, including the trailing slash:

```python
# This configuration...
auth_provider = AuthProvider(
    zone_id="your-zone-id",
    mcp_base_url="http://localhost:8000"  # No trailing slash
)

# Will become "http://localhost:8000/" internally
# So your Keycard Resource must be configured as: http://localhost:8000/
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

## Testing

This section provides comprehensive guidance on testing your FastMCP servers that use Keycard authentication. The examples show how to use the `mock_access_context` utility to easily mock authentication without needing to understand the internal SDK implementation.

### Overview

When testing FastMCP servers with Keycard authentication, you need to mock the authentication system. The `mock_access_context` utility provides four main testing scenarios:

1. **Default token** - Always returns a default access token for any resource
2. **Custom token** - Returns a specific access token for any resource  
3. **Resource-specific tokens** - Returns different tokens for different resources
4. **Error scenarios** - Simulates authentication failures

### Basic Test Setup

### Testing Tools With Grant Decorators

For tools that use the `@grant` decorator, use the `mock_access_context` utility to mock the authentication system:

#### 1. Default Token (Simple Case)

```python
@pytest.mark.asyncio
async def test_tool_with_default_token(auth_provider):
    """Test a tool with default access token."""
    
    # Create FastMCP server
    mcp = FastMCP("Test Server", auth=auth_provider.get_remote_auth_provider())
    
    @mcp.tool()
    @auth_provider.grant("https://api.example.com")
    def call_external_api(ctx: Context, query: str) -> str:
        access_context = ctx.get_state("keycardai")
        
        if access_context.has_errors():
            return f"Error: {access_context.get_errors()}"
        
        token = access_context.access("https://api.example.com").access_token
        return f"API result for {query} with token {token}"
    
    # Test with default token
    with mock_access_context():  # Uses "test_access_token" by default
        async with Client(mcp) as client:
            result = await client.call_tool("call_external_api", {"query": "test"})
    
    assert result is not None
    assert "test_access_token" in result.data
    assert "API result for test" in result.data
```

#### 2. Custom Token

```python
@pytest.mark.asyncio
async def test_tool_with_custom_token(auth_provider):
    """Test a tool with a specific access token."""
    
    mcp = FastMCP("Test Server", auth=auth_provider.get_remote_auth_provider())
    
    @mcp.tool()
    @auth_provider.grant("https://api.example.com")
    def call_external_api(ctx: Context, query: str) -> str:
        access_context = ctx.get_state("keycardai")
        token = access_context.access("https://api.example.com").access_token
        return f"API result for {query} with token {token}"
    
    # Test with custom token
    with mock_access_context(access_token="my_custom_token_123"):
        async with Client(mcp) as client:
            result = await client.call_tool("call_external_api", {"query": "test"})
    
    assert "my_custom_token_123" in result.data
```

#### 3. Resource-Specific Tokens

```python
@pytest.mark.asyncio
async def test_tool_with_resource_specific_tokens(auth_provider):
    """Test a tool with different tokens for different resources."""
    
    mcp = FastMCP("Test Server", auth=auth_provider.get_remote_auth_provider())
    
    @mcp.tool()
    @auth_provider.grant(["https://api.example.com", "https://calendar-api.com"])
    def sync_data(ctx: Context) -> str:
        access_context = ctx.get_state("keycardai")
        
        api_token = access_context.access("https://api.example.com").access_token
        calendar_token = access_context.access("https://calendar-api.com").access_token
        
        return f"API: {api_token}, Calendar: {calendar_token}"
    
    # Test with resource-specific tokens
    with mock_access_context(resource_tokens={
        "https://api.example.com": "api_token_123",
        "https://calendar-api.com": "calendar_token_456"
    }):
        async with Client(mcp) as client:
            result = await client.call_tool("sync_data", {})
    
    assert "api_token_123" in result.data
    assert "calendar_token_456" in result.data
```

### Testing Error Scenarios

Test how your tools handle authentication errors using the `has_errors` parameter:

```python
@pytest.mark.asyncio
async def test_tool_with_authentication_error(auth_provider):
    """Test tool behavior when authentication fails."""
    
    mcp = FastMCP("Test Server", auth=auth_provider.get_remote_auth_provider())
    
    @mcp.tool()
    @auth_provider.grant("https://api.example.com")
    def failing_tool(ctx: Context, query: str) -> str:
        access_context = ctx.get_state("keycardai")
        
        # Always check for errors first
        if access_context.has_errors():
            return f"Authentication failed: {access_context.get_errors()}"
        
        token = access_context.access("https://api.example.com").access_token
        return f"Success: {query}"
    
    # Test with authentication error
    with mock_access_context(has_errors=True, error_message="Token exchange failed"):
        async with Client(mcp) as client:
            result = await client.call_tool("failing_tool", {"query": "test"})
    
    assert result is not None
    assert "Authentication failed" in result.data
    assert "Token exchange failed" in result.data

@pytest.mark.asyncio
async def test_tool_with_custom_error_message(auth_provider):
    """Test tool with custom error message."""
    
    mcp = FastMCP("Test Server", auth=auth_provider.get_remote_auth_provider())
    
    @mcp.tool()
    @auth_provider.grant("https://api.example.com")
    def error_handling_tool(ctx: Context) -> str:
        access_context = ctx.get_state("keycardai")
        
        if access_context.has_errors():
            return f"Error occurred: {access_context.get_errors()}"
        
        return "Success"
    
    # Test with custom error message
    with mock_access_context(has_errors=True, error_message="Custom auth error"):
        async with Client(mcp) as client:
            result = await client.call_tool("error_handling_tool", {})
    
    assert "Custom auth error" in result.data
```

## Examples

For complete examples and advanced usage patterns, see our [documentation](https://docs.keycard.ai).

## License

MIT License - see [LICENSE](https://github.com/keycardai/python-sdk/blob/main/LICENSE) file for details.

## Support

- 📖 [Documentation](https://docs.keycard.ai)
- 🐛 [Issue Tracker](https://github.com/keycardai/python-sdk/issues)
- 📧 [Support Email](mailto:support@keycard.ai)
