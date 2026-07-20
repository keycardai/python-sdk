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
`access.has_errors()` / `access.get_errors()`), never raised. Granting
multiple resources is all-or-nothing: if any exchange fails, the context
carries that resource's error and no tokens.

If you lint with flake8-bugbear or Ruff's `B008` rule (function call in
argument default), exempt your tool modules: the call-in-default is the
intended spelling here, the same pattern as FastAPI's `Depends`. In
`pyproject.toml`:

```toml
[tool.ruff.lint.per-file-ignores]
"src/my_server/tools/*.py" = ["B008"]
```

### Migrating from the decorator form

The decorator form (`@auth_provider.grant(...)` above the tool) still works
from the same object. Reading the result via `ctx.get_state("keycardai")` is
deprecated and emits a `DeprecationWarning`; helpers that only hold the
FastMCP `Context` can use `await AccessContext.from_context(ctx)` instead.

The warning fires once per tool, at decoration time (module import). If your
test or CI setup escalates warnings to errors (`-W error`,
`filterwarnings = ["error"]` in pytest config), importing a server module
that still uses the old form will raise instead of warn. Either migrate the
tools to the injected-parameter form, or allow this warning explicitly:

```toml
filterwarnings = ["error", 'default:Tool .* uses the grant decorator:DeprecationWarning']
```

## Testing

Fake delegated access without patching internals:

```python
from keycardai.fastmcp.testing import mock_access_context

with mock_access_context(access_token="fake_token"):
    ...  # grants resolve to an AccessContext serving fake_token
```

The bare `access_token` form serves the token for any resource, so it will
not catch a mistyped resource URL in an `access(...)` call. Pass
`resource_tokens={...}` when the test should enforce which resources the
tool reads; resources outside the dict raise `ResourceAccessError`, matching
production.

## Migration from `keycardai-mcp-fastmcp`

The old package keeps working: `from keycardai.mcp.integrations.fastmcp import AuthProvider`
emits a `DeprecationWarning` pointing here and returns the same class. Migrate
when convenient.
