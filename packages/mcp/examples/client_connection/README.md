# MCP Client Connection with Keycard OAuth

A complete example demonstrating how to connect to an MCP server as a client using OAuth authentication with `StarletteAuthCoordinator`.

## Why Keycard?

Keycard enables secure OAuth authentication for MCP connections. This example shows the client-side flow:

- **Connect to authenticated MCP servers** using OAuth 2.0
- **Handle auth challenges** with automatic redirect URL generation
- **Persist tokens** across application restarts with SQLite storage

## Prerequisites

Before running this example:

### 1. Sign up at [keycard.ai](https://keycard.ai)

### 2. Create a Zone

Create an authentication zone in the Keycard console.

### 3. Start an Authenticated MCP Server

This example connects to an MCP server. Start the `hello_world_server` example first:

```bash
cd ../hello_world_server
export KEYCARD_ZONE_ID="your-zone-id"
uv sync && uv run python main.py
```

The server will start on `http://localhost:8000`.

## Quick Start

### 1. Set Environment Variables (Optional)

```bash
# Default values work for local development
export MCP_SERVER_URL="http://localhost:8000/mcp"
export CALLBACK_HOST="localhost"
export CALLBACK_PORT="8080"
```

### 2. Install Dependencies

```bash
cd packages/mcp/examples/client_connection
uv sync
```

### 3. Run the Client

```bash
uv run python main.py
```

### 4. Open in Browser

Navigate to http://localhost:8080/ and follow the authentication flow:

1. Click "Authenticate" to start OAuth flow
2. Complete authentication with Keycard
3. Refresh the page to see connected status
4. Test calling the `hello_world` tool

## How It Works

### Connection Status Lifecycle

```
INITIALIZING
     |
     v
CONNECTING ---> AUTHENTICATING ---> AUTH_PENDING (waiting for user)
     |                                    |
     v                                    v
CONNECTION_FAILED                    CONNECTED (after OAuth callback)
```

### OAuth Flow

```
Client                    Browser                 Keycard                MCP Server
  |                          |                       |                       |
  |-- connect() ------------>|                       |                       |
  |                          |                       |<-- 401 Unauthorized --|
  |<-- AUTH_PENDING ---------|                       |                       |
  |                          |                       |                       |
  |-- get_auth_challenges() -|                       |                       |
  |-- (show auth URL) ------>|                       |                       |
  |                          |-- User authenticates ->|                       |
  |                          |<-- Redirect to callback|                       |
  |<-- OAuth callback -------|                       |                       |
  |                          |                       |                       |
  |-- (auto-reconnect) ----->|                       |                       |
  |<-- CONNECTED ------------|                       |<-- Authenticated -----|
```

1. Client attempts to connect to MCP server
2. Server returns 401, triggering OAuth challenge
3. Client generates authorization URL and sets status to `AUTH_PENDING`
4. User authenticates in browser
5. OAuth callback is received at `/oauth/callback`
6. Tokens are stored, session auto-reconnects
7. Status becomes `CONNECTED`

## Session Status Properties

| Property | Description |
|----------|-------------|
| `is_operational` | `True` when fully connected and ready to call tools |
| `requires_user_action` | `True` when waiting for OAuth completion |
| `is_failed` | `True` when in a failure state |
| `can_retry` | `True` when reconnection is possible |

## Key Patterns Demonstrated

### 1. Setting up StarletteAuthCoordinator

```python
from keycardai.mcp.client import StarletteAuthCoordinator, SQLiteBackend

storage = SQLiteBackend("client_auth.db")
coordinator = StarletteAuthCoordinator(
    backend=storage,
    redirect_uri="http://localhost:8080/oauth/callback"
)
```

### 2. Creating an OAuth-Enabled Client

```python
from keycardai.mcp.client import Client

SERVERS = {
    "my-server": {
        "url": "http://localhost:8000/mcp",
        "transport": "streamable-http",
        "auth": {"type": "oauth"},
    }
}

client = Client(
    servers=SERVERS,
    storage_backend=storage,
    auth_coordinator=coordinator,
)
await client.connect()
```

### 3. Checking Session Status

```python
session = client.sessions["my-server"]

if session.requires_user_action:
    # User needs to authenticate
    challenges = await client.get_auth_challenges()
    auth_url = challenges[0]["authorization_url"]
    print(f"Please authenticate: {auth_url}")

elif session.is_operational:
    # Ready to call tools
    result = await client.call_tool("hello_world", {"name": "World"})
```

### 4. Registering the Callback Endpoint

```python
from starlette.routing import Route

app = Starlette(routes=[
    Route("/oauth/callback", coordinator.get_completion_endpoint()),
])
```

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MCP_SERVER_URL` | No | `http://localhost:8000/mcp` | URL of MCP server to connect to |
| `CALLBACK_HOST` | No | `localhost` | Host for callback server |
| `CALLBACK_PORT` | No | `8080` | Port for callback server |

## Troubleshooting

### "Session stuck in AUTH_PENDING"

- Ensure you completed the OAuth flow in the browser
- Check that the callback URL matches what's configured in Keycard
- Refresh the page after authentication

### "Connection refused to MCP server"

- Verify the `hello_world_server` is running on port 8000
- Check `MCP_SERVER_URL` environment variable

### "OAuth callback not received"

- Ensure `CALLBACK_HOST` and `CALLBACK_PORT` are accessible
- For remote development, use a tunnel (ngrok, Cloudflare Tunnel)

## Learn More

- [Keycard Documentation](https://docs.keycard.ai)
- [MCP Client SDK Documentation](https://docs.keycard.ai/sdk/python/client)
- [Hello World Server Example](../hello_world_server/)
