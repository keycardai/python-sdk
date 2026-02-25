# Keycard Python SDK

**Keycard handles OAuth, identity, and access so your MCP servers don't have to.** Add authentication, authorization, and delegated API access to any MCP server with a few lines of Python — no token plumbing, no auth middleware, no security footguns.

- **Drop-in auth** for MCP servers (OAuth 2.0, PKCE, token exchange — handled for you)
- **Delegated access** — call Google, GitHub, Slack APIs on behalf of your users with automatic token exchange
- **Works with both** the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) and the [FastMCP](https://github.com/jlowin/fastmcp) framework

## Which Package?

| You want to... | Install | Guide |
|---|---|---|
| Add auth to MCP servers (using the `mcp` SDK) | `pip install keycardai-mcp` | [Quick Start](#quick-start-standard-mcp) |
| Add auth to FastMCP servers | `pip install keycardai-mcp-fastmcp` | [Quick Start](#quick-start-fastmcp) |
| Connect to MCP servers as a client | `pip install keycardai-mcp` | [MCP Client docs](packages/mcp/src/keycardai/mcp/client/) |
| Build agent-to-agent (A2A) services | `pip install keycardai-agents` | [Agents docs](packages/agents/) |
| Use the OAuth 2.0 client directly | `pip install keycardai-oauth` | [OAuth docs](packages/oauth/) |

## Key Concepts

- **Zone** — A Keycard environment that groups your identity providers, MCP resources, and access policies. Get your zone ID from [console.keycard.ai](https://console.keycard.ai).
- **Delegated Access** — Calling external APIs (Google, GitHub, Slack, etc.) on behalf of your authenticated users via [RFC 8693](https://datatracker.ietf.org/doc/html/rfc8693) token exchange.
- **`@grant` decorator** — Declares which external APIs a tool needs. Automatically exchanges the user's token for a scoped token before your function runs.
- **AccessContext** — The result of a grant. Contains exchanged tokens or errors. Non-throwing by design — always check `.has_errors()` before using tokens.
- **Application Credentials** — How your server authenticates with Keycard for token exchange. Three types: `ClientSecret`, `WebIdentity`, `EKSWorkloadIdentity`.

## Known Limitations & Non-Goals

### Current Limitations

- **Alpha Status**: All packages are in early development (`Development Status :: 3 - Alpha`). APIs may change between minor versions.
- **FastMCP 3.x Not Supported**: The `keycardai-mcp-fastmcp` package is pinned to FastMCP 2.x due to breaking async API changes in FastMCP 3.0 (see [PR #49](https://github.com/keycardai/python-sdk/pull/49)). Support for 3.x will be evaluated once the API stabilizes.
- **MCP Protocol Version**: Tested against MCP protocol version as implemented by `mcp>=1.13.1`. Newer MCP protocol versions may introduce incompatibilities.

### Non-Goals

- **Standalone Identity Provider**: Keycard SDKs are designed to integrate with Keycard's hosted identity service, not to provide standalone identity management.
- **Multi-Language Support**: This SDK is Python-only. Other language SDKs are separate projects.
- **Offline Operation**: All authentication flows require network connectivity to Keycard services.

## Compatibility Matrix

### Python Version Support

| Python Version | Status |
|---------------|--------|
| 3.9 | Not Supported |
| 3.10 | Supported (minimum) |
| 3.11 | Supported |
| 3.12 | Supported |
| 3.13 | Supported |

### Key Dependency Constraints

| Package | Dependency | Version Constraint | Rationale |
|---------|------------|-------------------|-----------|
| `keycardai-mcp-fastmcp` | `fastmcp` | `>=2.13.0,<3.0.0` | FastMCP 3.x has breaking async API changes. Constraint will be lifted when migration is complete. |
| All packages | `pydantic` | `>=2.11.7` | No upper bound - Pydantic 2.x maintains backward compatibility. |
| All packages | `httpx` | `>=0.27.2` | No upper bound - httpx follows semver. |
| `keycardai-mcp` | `mcp` | `>=1.13.1` | No upper bound - API is protocol-defined. |

### Why No Upper Bounds on Most Dependencies?

Following Python packaging best practices:
1. **Upper bounds cause resolver conflicts** in end-user projects when multiple packages specify conflicting ranges.
2. **Well-maintained libraries** (pydantic, httpx) follow semantic versioning and maintain backward compatibility.
3. **Testing against latest** via CI catches issues before users encounter them.

## Versioning & Breaking Changes

### Versioning Strategy

All packages follow [Semantic Versioning](https://semver.org/):

- **MAJOR.MINOR.PATCH** (e.g., `0.15.0`)
- During `0.x.y` development:
  - **MINOR** bumps (`0.x.0`) may contain breaking changes
  - **PATCH** bumps (`0.x.y`) are backward-compatible bug fixes

### Alpha Status (`0.x.y`)

All packages are currently in alpha status. This means:

1. **API Stability**: Public APIs may change between minor versions
2. **Documentation**: APIs are documented but may evolve
3. **Production Use**: Suitable for early adopters comfortable with potential migration work

### When Will Packages Reach 1.0?

Packages will graduate to `1.0.0` when:
- Public API is stable and well-documented
- Comprehensive test coverage exists
- Production usage validates the design
- No planned breaking changes for foreseeable future

### Breaking Change Policy

1. **Breaking changes** are documented in CHANGELOG.md with migration guides
2. **Deprecation warnings** will be added before removal where feasible
3. **Commit messages** use `!` suffix (e.g., `feat!:`) for breaking changes
4. **Release notes** highlight breaking changes prominently

> **`mcp` vs `fastmcp`:** The `mcp` SDK includes a built-in `FastMCP` class (`from mcp.server.fastmcp import FastMCP`), while `fastmcp` is a separate standalone framework (`from fastmcp import FastMCP`). They're different libraries. `keycardai-mcp` wraps the former; `keycardai-mcp-fastmcp` wraps the latter.

## Prerequisites

1. **Python 3.10+** and a virtual environment
2. Sign up at [keycard.ai](https://keycard.ai) and get your **zone ID** from Zone Settings
3. Configure an identity provider (Google, Microsoft, etc.) and create an MCP resource in your zone

## Quick Start: FastMCP

```bash
uv add keycardai-mcp-fastmcp fastmcp
```

```python
from fastmcp import FastMCP
from keycardai.mcp.integrations.fastmcp import AuthProvider

# Configure Keycard authentication
auth_provider = AuthProvider(
    zone_id="your-zone-id",  # From console.keycard.ai
    mcp_server_name="My Server",
    mcp_base_url="http://localhost:8000/"
)

# Create authenticated MCP server
auth = auth_provider.get_remote_auth_provider()
mcp = FastMCP("My Server", auth=auth)

@mcp.tool()
def hello_world(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

See the [FastMCP examples](packages/mcp-fastmcp/examples/) for runnable projects.

## Quick Start: Standard MCP

```bash
pip install keycardai-mcp uvicorn
```

```python
from mcp.server.fastmcp import FastMCP
from keycardai.mcp.server.auth import AuthProvider
import uvicorn

# Your MCP server
mcp = FastMCP("My Server")

@mcp.tool()
def hello_world(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"

# Add Keycard authentication
auth_provider = AuthProvider(
    zone_id="your-zone-id",  # From console.keycard.ai
    mcp_server_name="My Server",
    mcp_server_url="http://localhost:8000/"
)

# Wrap with auth and run
app = auth_provider.app(mcp)
uvicorn.run(app, host="0.0.0.0", port=8000)
```

See the [MCP examples](packages/mcp/examples/) for runnable projects.

## Delegated Access

Delegated access lets your MCP tools call external APIs (Google Calendar, GitHub, Slack, etc.) on behalf of authenticated users via automatic token exchange.

**Setup:** Get client credentials from [console.keycard.ai](https://console.keycard.ai), then set `KEYCARD_CLIENT_ID` and `KEYCARD_CLIENT_SECRET` as environment variables.

### FastMCP

```python
import os
import httpx
from fastmcp import FastMCP, Context
from keycardai.mcp.integrations.fastmcp import AuthProvider, AccessContext, ClientSecret

auth_provider = AuthProvider(
    zone_id="your-zone-id",
    mcp_server_name="My Server",
    mcp_base_url="http://localhost:8000/",
    application_credential=ClientSecret((
        os.getenv("KEYCARD_CLIENT_ID"),
        os.getenv("KEYCARD_CLIENT_SECRET")
    ))
)

auth = auth_provider.get_remote_auth_provider()
mcp = FastMCP("My Server", auth=auth)

@mcp.tool()
@auth_provider.grant("https://www.googleapis.com/calendar/v3")
async def get_calendar_events(ctx: Context) -> dict:
    """Get the user's calendar events with delegated access."""
    # Retrieve access context from FastMCP context
    access_context: AccessContext = ctx.get_state("keycardai")

    if access_context.has_errors():
        return {"error": f"Token exchange failed: {access_context.get_errors()}"}

    token = access_context.access("https://www.googleapis.com/calendar/v3").access_token

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization": f"Bearer {token}"}
        )
        response.raise_for_status()
        return response.json()
```

### Standard MCP

```python
import os
import httpx
from mcp.server.fastmcp import FastMCP, Context
from keycardai.mcp.server.auth import AuthProvider, AccessContext, ClientSecret

auth_provider = AuthProvider(
    zone_id="your-zone-id",
    mcp_server_name="My Server",
    mcp_server_url="http://localhost:8000/",
    application_credential=ClientSecret((
        os.getenv("KEYCARD_CLIENT_ID"),
        os.getenv("KEYCARD_CLIENT_SECRET")
    ))
)

mcp = FastMCP("My Server")

@mcp.tool()
@auth_provider.grant("https://www.googleapis.com/calendar/v3")
async def get_calendar_events(access_ctx: AccessContext, ctx: Context) -> dict:
    """Get the user's calendar events with delegated access."""
    # @grant requires both AccessContext (for tokens) and Context (for request state)
    if access_ctx.has_errors():
        return {"error": f"Token exchange failed: {access_ctx.get_errors()}"}

    token = access_ctx.access("https://www.googleapis.com/calendar/v3").access_token

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization": f"Bearer {token}"}
        )
        response.raise_for_status()
        return response.json()

app = auth_provider.app(mcp)
```

> **Key difference:** In `keycardai-mcp`, the `@grant` decorator requires both `access_ctx: AccessContext` and `ctx: Context` as function parameters. In `keycardai-mcp-fastmcp`, `AccessContext` is retrieved from the FastMCP `Context` via `ctx.get_state("keycardai")`.

For complete delegated access examples with error handling patterns, see:
- [FastMCP delegated access example](packages/mcp-fastmcp/examples/delegated_access/)
- [Standard MCP delegated access example](packages/mcp/examples/delegated_access/)

## Connecting Your AI Client

Configure the remote MCP in your AI client (e.g., [Cursor](https://cursor.com)):

```json
{
  "mcpServers": {
    "my-server": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

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

> [!WARNING]
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
    zone_id="your-zone-id",
    mcp_server_name="My Server",
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
    zone_id="your-zone-id",
    mcp_server_name="My Server",
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

## FAQ

### How to test the MCP server with modelcontextprotocol/inspector?

When testing your MCP server with the [modelcontextprotocol/inspector](https://github.com/modelcontextprotocol/inspector), you may need to configure CORS to allow the inspector's web interface to access your protected endpoints from localhost.

**Note:** This applies specifically to `keycardai-mcp`. When using `keycardai-mcp-fastmcp`, no middleware is currently required as FastMCP permits access to metadata endpoints by default.

```python
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],  # For local dev only
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
]

app = auth_provider.app(mcp, middleware=middleware)
```

**Important:** The `allow_origins=["*"]` setting is for **local development only**. In production, restrict to specific domains.

## Documentation

- [Full documentation](https://docs.keycard.ai) — API reference, tutorials, integration guides
- **Package docs:**
  - [keycardai-mcp](packages/mcp/) — MCP server authentication
  - [keycardai-mcp-fastmcp](packages/mcp-fastmcp/) — FastMCP integration
  - [keycardai-mcp client](packages/mcp/src/keycardai/mcp/client/) — MCP client (CLI, web apps, AI agent integrations)
  - [keycardai-agents](packages/agents/) — Agent-to-agent delegation (A2A)
  - [keycardai-oauth](packages/oauth/) — OAuth 2.0 client
- **Examples:** [MCP](packages/mcp/examples/) · [FastMCP](packages/mcp-fastmcp/examples/) · [OAuth](packages/oauth/examples/) · [Agents](packages/agents/examples/)

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

- GitHub Issues: [https://github.com/keycardai/python-sdk/issues](https://github.com/keycardai/python-sdk/issues)
- Documentation: [https://docs.keycard.ai](https://docs.keycard.ai/)
- Email: support@keycard.ai
