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
from fastmcp import FastMCP
from keycardai.fastmcp import AccessContext, AuthProvider

auth_provider = AuthProvider(
    zone_id="abc1234",
    mcp_server_name="My Server",
    mcp_base_url="http://localhost:8000",
)

mcp = FastMCP("My Server", auth=auth_provider.get_remote_auth_provider())

@mcp.tool()
async def call_external_api(
    query: str,
    access: AccessContext = auth_provider.grant("https://api.example.com"),
):
    token = access.access("https://api.example.com").access_token
    return f"Results for {query} (token starts with {token[:8]})"
```

Declaring the grant as a typed parameter default injects the populated
`AccessContext` per request; the parameter never appears in the tool's input
schema. Exchange failures are recorded on the `AccessContext` (check
`access.has_errors()` / `access.get_errors()`), never raised.

The decorator form (`@auth_provider.grant(...)` above the tool) still works
from the same object. Reading the result via `ctx.get_state("keycardai")` is
deprecated and emits a `DeprecationWarning`; helpers that only hold the
FastMCP `Context` can use `await AccessContext.from_context(ctx)` instead.

## Testing

Fake delegated access without patching internals:

```python
from keycardai.fastmcp.testing import mock_access_context

with mock_access_context(access_token="fake_token"):
    ...  # grants resolve to an AccessContext serving fake_token
```

## Migration from `keycardai-mcp-fastmcp`

The old package keeps working: `from keycardai.mcp.integrations.fastmcp import AuthProvider`
emits a `DeprecationWarning` pointing here and returns the same class. Migrate
when convenient.
