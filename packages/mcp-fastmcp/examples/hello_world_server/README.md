# Hello World MCP Server with Keycard Authentication

A minimal example demonstrating how to add Keycard authentication to a FastMCP server.

## Why Keycard?

Keycard lets you securely connect your AI IDE or agent to external resources. It provides OAuth-based authentication for your MCP server plus auditability—so you know who accessed what.

## Prerequisites

Before running this example, set up Keycard:

1. **Sign up** at [keycard.ai](https://keycard.ai)
2. **Create a zone** — this is your authentication boundary
3. **Configure an identity provider** (Google, Microsoft, etc.) — this is how your users will sign in
4. **Create an MCP resource** with URL `http://localhost:8000/` — this registers your server with Keycard

Once configured, get your **zone ID** from the Keycard console. See [MCP Server Setup](https://docs.keycard.ai/build-with-keycard/mcp-server) for detailed instructions.

## Quick Start

### 1. Set Environment Variables

```bash
export KEYCARD_ZONE_ID="your-zone-id"
export MCP_SERVER_URL="http://localhost:8000/"
```

### 2. Install Dependencies

```bash
cd packages/mcp-fastmcp/examples/hello_world_server
uv sync
```

### 3. Run the Server

```bash
uv run python main.py
```

The server will start on `http://localhost:8000`.

### 4. Verify the Server

Check that OAuth metadata is being served:

```bash
curl http://localhost:8000/.well-known/oauth-authorization-server
```

You should see JSON with `issuer`, `authorization_endpoint`, and other OAuth metadata.

## Testing with MCP Client

Connect to your server using any MCP-compatible client (e.g., Cursor, Claude Desktop) and authenticate through your configured identity provider.

## Delegated Access

For accessing external APIs on behalf of users using the `@grant` decorator, see the [Delegated Access Example](../delegated_access/).

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `KEYCARD_ZONE_ID` | Yes | Your Keycard zone ID |
| `MCP_SERVER_URL` | No | Server URL (default: `http://localhost:8000/`) |

## Learn More

- [Keycard Documentation](https://docs.keycard.ai)
- [FastMCP Documentation](https://docs.fastmcp.com)
