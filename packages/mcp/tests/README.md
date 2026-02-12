# Test Infrastructure

## Running Tests

### Default Tests (Mocked)

Run all tests without any environment variables:

```bash
uv run pytest packages/mcp/tests -v
```

These tests use mock fixtures and don't require real Keycard infrastructure.

### Interactive Tests (Real Infrastructure)

Tests in `integration/interactive/` require a real Keycard zone:

```bash
export KEYCARD_ZONE_URL="https://your-zone.keycard.cloud"
export OPENAI_API_KEY="sk-..."  # Optional, for agent tests
export RUN_INTERACTIVE_TESTS=1

uv run pytest packages/mcp/tests/integration/interactive/ -v -s
```

## Test Organization

| Directory | Purpose | Infrastructure |
|-----------|---------|----------------|
| `keycardai/` | Unit tests | Mocked |
| `integration/e2e/` | End-to-end flows | Mocked |
| `integration/interactive/` | Manual OAuth flows | Real Keycard |

## Environment Variables

| Variable | Required For | Description |
|----------|--------------|-------------|
| `RUN_INTERACTIVE_TESTS` | Interactive tests | Set to `1` to enable |
| `KEYCARD_ZONE_URL` | Interactive tests | Real Keycard zone URL |
| `OPENAI_API_KEY` | Agent integration tests | OpenAI API key |
| `MCP_TEST_PORT` | Interactive tests | Custom port (default: 8765) |

## Mocking Patterns

Tests use dependency injection of mock clients via `client_factory` parameter.
See `fixtures/auth_provider.py` for reusable mock fixtures.
