# GitHub API Integration with Keycard Delegated Access

A complete example demonstrating how to use the `@grant` decorator for token exchange, enabling your MCP server to access external APIs (GitHub) on behalf of authenticated users.

## Why Keycard?

Keycard lets you securely connect your AI IDE or agent to external resources. With delegated access, your MCP server can:

- **Exchange user tokens** for API-specific access tokens via OAuth 2.0 Token Exchange
- **Access external APIs** on behalf of authenticated users with proper scopes
- **Maintain audit trails** of all delegated operations

## Prerequisites

Before running this example, set up Keycard for delegated access:

### 1. Sign up at [keycard.ai](https://keycard.ai)

### 2. Create a Zone

A zone is your authentication boundary. Create one in the Keycard console.

### 3. Configure an Identity Provider

Set up an identity provider (Google, Microsoft, etc.) for user authentication.

### 4. Create a GitHub App

Create a GitHub App in your organization (or personal account) to enable delegated access:

1. Go to your GitHub organization → **Settings** → **Developer settings** → **GitHub Apps**
2. Click **New GitHub App**
3. Configure the app with the permissions your MCP server needs (e.g., **Repository** access, **User** profile read)
4. Generate a **Client Secret**
5. Note down the **Client ID** and **Client Secret** — you'll use these to configure the credential provider in Keycard

### 5. Configure a Credential Provider in Keycard

Set up the GitHub credential provider so Keycard can obtain tokens on behalf of users:

1. In your Keycard zone, go to **Providers**
2. Add a new GitHub credential provider using the **Client ID** and **Client Secret** from the GitHub App you created in step 4
3. This credential provider is what Keycard uses to issue GitHub API tokens on behalf of authenticated users

### 6. Configure GitHub as an API Resource

Add GitHub as an API resource in your zone:

1. In your Keycard zone, go to **Resources**
2. Add a new API resource for GitHub:
   - **Resource URL**: `https://api.github.com`
   - **Credential Provider**: Select the GitHub credential provider you created in step 5
   - **Scopes**: `read:user`, `repo` (adjust based on your needs)

### 7. Create an Application

Create an application that will represent your MCP server:

1. Go to **Applications** in your zone
2. Create a new application
   - **Identifier**: Set this to match your `MCP_SERVER_URL` (e.g., `http://localhost:8000/`)
3. Add the **GitHub API** resource as a dependency of this application
4. Generate **Application Credentials** (Client ID and Client Secret)
   - These are what you'll use for `KEYCARD_CLIENT_ID` and `KEYCARD_CLIENT_SECRET`

### 8. Create an MCP Server Resource

Register your MCP server with Keycard:

1. Go to **Resources** and add a new MCP Server resource
2. Set the URL to your server's MCP endpoint: `http://localhost:8000/mcp`
3. Configure the resource:
   - **Provided by**: Select the application you created in step 7
   - **Credential Provider**: Keycard STS Zone Provider

> **Note:** Delegated token exchange requires Keycard to reach your MCP server. For local development, use a tunneling service (e.g., ngrok, Cloudflare Tunnel) or host the server on a publicly accessible URL.

See [Delegated Access Setup](https://docs.keycard.ai/build-with-keycard/delegated-access) for detailed instructions.

## Quick Start

### 1. Set Up Tunneling (for local development)

Delegated access requires Keycard to reach your server. For local development, set up a tunnel:

```bash
# Using ngrok
ngrok http 8000

# Or using Cloudflare Tunnel
cloudflared tunnel --url http://localhost:8000
```

Use the public URL from your tunnel as `MCP_SERVER_URL`.

### 2. Set Environment Variables

```bash
export KEYCARD_ZONE_ID="your-zone-id"
export KEYCARD_CLIENT_ID="your-client-id"
export KEYCARD_CLIENT_SECRET="your-client-secret"
export MCP_SERVER_URL="https://your-tunnel-url.ngrok.io/"  # Must be publicly reachable
```

### 3. Install Dependencies

```bash
cd packages/mcp-fastmcp/examples/delegated_access
uv sync
```

### 4. Run the Server

```bash
uv run python main.py
```

The server will start on `http://localhost:8000`.

### 5. Verify the Server

Check that OAuth metadata is being served:

```bash
curl http://localhost:8000/.well-known/oauth-authorization-server
```

You should see JSON with `issuer`, `authorization_endpoint`, and other OAuth metadata.

## Testing with MCP Client

1. Connect to your server using an MCP-compatible client (e.g., Cursor, Claude Desktop)
2. Authenticate through your configured identity provider
3. When prompted by Keycard, authorize GitHub access
4. Call the `get_github_user` or `list_github_repos` tools
5. Verify GitHub user data is returned

## How It Works

### Token Exchange Flow

```
User                    MCP Server              Keycard                 GitHub
  │                         │                      │                       │
  │──── Authenticate ──────►│                      │                       │
  │                         │◄── User Token ───────│                       │
  │                         │                      │                       │
  │──── Call Tool ─────────►│                      │                       │
  │                         │── Exchange Token ───►│                       │
  │                         │◄─ GitHub Token ──────│                       │
  │                         │                      │                       │
  │                         │──────────────────────┼── API Request ───────►│
  │                         │◄─────────────────────┼── API Response ───────│
  │◄─── Tool Result ────────│                      │                       │
```

1. User authenticates to your MCP server via Keycard
2. When a tool with `@grant` is called, Keycard exchanges the user's token
3. The exchanged token has the scopes configured for the external resource
4. Your server uses this token to call GitHub API on behalf of the user

## Error Handling

The example demonstrates comprehensive error handling patterns:

| Method | Description |
|--------|-------------|
| `has_errors()` | Check for any errors (global or resource-specific) |
| `get_errors()` | Get all error details as a dictionary |
| `has_resource_error(url)` | Check for errors on a specific resource |
| `get_resource_errors(url)` | Get errors for a specific resource |
| `has_error()` | Check for global errors only |
| `get_error()` | Get global error details |

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `KEYCARD_ZONE_ID` | Yes | Your Keycard zone ID |
| `KEYCARD_CLIENT_ID` | Yes | Client ID from application credentials |
| `KEYCARD_CLIENT_SECRET` | Yes | Client secret from application credentials |
| `MCP_SERVER_URL` | Yes | Server URL (must be publicly reachable for delegated access) |

## Learn More

- [Keycard Documentation](https://docs.keycard.ai)
- [Delegated Access Guide](https://docs.keycard.ai/build-with-keycard/delegated-access)
- [FastMCP Documentation](https://docs.fastmcp.com)
- [GitHub API Documentation](https://docs.github.com/rest)
