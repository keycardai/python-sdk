# OAuth Client Usage (PKCE Authentication)

A minimal example showing how to use `AgentClient` with automatic PKCE user authentication to call protected agent services.

## Why Keycard?

Keycard handles OAuth for agent-to-agent communication. When your client calls a protected agent service, Keycard automatically opens the browser for user login, exchanges tokens, and retries the call — no manual token management needed.

## Prerequisites

Before running this example, set up Keycard:

1. **Sign up** at [keycard.ai](https://keycard.ai)
2. **Create a zone** — this is your authentication boundary
3. **Configure an identity provider** (Google, Microsoft, etc.) — this is how your users will sign in
4. **Register a public OAuth client** in the Keycard console (PKCE clients don't need a secret)
5. **Have an agent service running** that your client will call

## When to Use This

- Building CLI tools that call protected agent services
- Creating client applications that need user-scoped access to agents
- Testing agent services with real OAuth flows

## Quick Start

### 1. Set Environment Variables

```bash
export KEYCARD_ZONE_ID="your-zone-id"
export KEYCARD_CLIENT_ID="your-public-client-id"
export AGENT_SERVICE_URL="https://your-agent-service.example.com"
```

### 2. Install Dependencies

```bash
cd packages/agents/examples/oauth_client_usage
uv sync
```

### 3. Run the Example

```bash
uv run python main.py
```

**What happens:**
1. The client attempts to call the protected agent service
2. On receiving a 401, it discovers OAuth configuration automatically
3. Your browser opens for login
4. After login, the token is cached and the call is retried
5. Subsequent calls reuse the cached token

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `KEYCARD_ZONE_ID` | Yes | Your Keycard zone ID |
| `KEYCARD_CLIENT_ID` | Yes | OAuth public client ID from Keycard console |
| `AGENT_SERVICE_URL` | No | URL of the agent service to call |
| `IDENTITY_URL` | No | Identity URL for your client app |

## Learn More

- [Agents Package README](../../) — Full documentation for the agents SDK
- [A2A JSONRPC Example](../a2a_jsonrpc_usage/) — Calling agents via the A2A protocol
- [Keycard Documentation](https://docs.keycard.ai)
