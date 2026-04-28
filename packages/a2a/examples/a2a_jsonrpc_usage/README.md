# A2A JSONRPC Usage

A minimal example showing how to call Keycard agent services using the standard A2A JSONRPC protocol.

## When to Use This

Keycard agent services expose two endpoints:

| Endpoint | Protocol | Best For |
|----------|----------|----------|
| `POST /a2a/jsonrpc` | A2A JSONRPC | Standards-compliant clients, streaming, task management |
| `POST /invoke` | Custom | Simple request/response, delegation chain tracking |

Use the A2A JSONRPC protocol when you need streaming, task cancellation, or interoperability with other A2A-compliant systems.

## Prerequisites

Before running this example, set up Keycard:

1. **Sign up** at [keycard.ai](https://keycard.ai)
2. **Create a zone** — this is your authentication boundary
3. **Configure an identity provider** (Google, Microsoft, etc.)
4. **Have an agent service running** that your client will call

## Quick Start

### 1. Set Environment Variables

```bash
export KEYCARD_ZONE_ID="your-zone-id"
export KEYCARD_CLIENT_ID="your-client-id"
export AGENT_SERVICE_URL="https://your-agent-service.example.com"
```

### 2. Install Dependencies

```bash
cd packages/agents/examples/a2a_jsonrpc_usage
uv sync
```

### 3. Run the Example

```bash
uv run python main.py
```

The example demonstrates three approaches:
1. **Manual JSONRPC** — Raw httpx request to `/a2a/jsonrpc`
2. **A2A SDK client** — Using the `a2a-sdk` package for typed requests
3. **Custom /invoke** — Using `AgentClient` for comparison

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `KEYCARD_ZONE_ID` | Yes | Your Keycard zone ID |
| `KEYCARD_CLIENT_ID` | Yes | OAuth client ID from Keycard console |
| `AGENT_SERVICE_URL` | No | URL of the agent service to call |
| `AUTH_TOKEN` | No | Bearer token for manual JSONRPC calls (Example 1) |

## Learn More

- [Agents Package README](../../) — Full documentation for the agents SDK
- [OAuth Client Example](../oauth_client_usage/) — PKCE user authentication
- [Keycard Documentation](https://docs.keycard.ai)
