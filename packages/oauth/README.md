# Keycard OAuth SDK

A comprehensive Python SDK for OAuth 2.0 functionality implementing multiple OAuth 2.0 standards for enterprise-grade token management.

## Requirements

- **Python 3.10 or greater**
- Virtual environment (recommended)

## Setup Guide

### Option 1: Using uv (Recommended)

If you have [uv](https://docs.astral.sh/uv/) installed:

```bash
# Create a new project with uv
uv init my-oauth-project
cd my-oauth-project

# Create and activate virtual environment
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### Option 2: Using Standard Python

```bash
# Create project directory
mkdir my-oauth-project
cd my-oauth-project

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Upgrade pip (recommended)
pip install --upgrade pip
```

## Installation

```bash
uv add keycardai-oauth
```

Or with pip:

```bash
pip install keycardai-oauth
```

## Quick Start

### Synchronous Client

For traditional applications that don't use async/await:

```python
from keycardai.oauth import Client, BasicAuth, TokenType

with Client(
    "https://oauth.example.com",
    auth=BasicAuth("your_client_id", "your_client_secret")
) as client:
    response = client.exchange_token(
        subject_token="original_access_token",
        subject_token_type=TokenType.ACCESS_TOKEN,
        audience="https://api.example.com"
    )
    print(f"New token: {response.access_token}")
    print(f"Expires in: {response.expires_in} seconds")
```

### Asynchronous Client

For async applications (FastAPI, aiohttp, etc.):

```python
import asyncio
from keycardai.oauth import AsyncClient, BasicAuth, TokenType

async def main():
    async with AsyncClient(
        "https://oauth.example.com",
        auth=BasicAuth("your_client_id", "your_client_secret")
    ) as client:
        response = await client.exchange_token(
            subject_token="original_access_token",
            subject_token_type=TokenType.ACCESS_TOKEN,
            audience="https://api.example.com"
        )
        print(f"New token: {response.access_token}")

asyncio.run(main())
```

## Features

- **Token Exchange (RFC 8693)** - Exchange tokens for different audiences, scopes, or token types
- **Dynamic Client Registration (RFC 7591)** - Register OAuth clients programmatically
- **Authorization Server Metadata (RFC 8414)** - Auto-discover server endpoints and capabilities
- **Bearer Token Support (RFC 6750)** - Standard bearer token handling and utilities
- **PKCE Support (RFC 7636)** - Proof Key for Code Exchange for public clients
- **Multiple Auth Strategies** - BasicAuth, BearerAuth, and multi-zone authentication
- **Comprehensive Error Handling** - Structured exceptions with retry guidance
- **Sync and Async Clients** - Choose the right client for your application

## OAuth Standards Supported

The SDK implements the following OAuth 2.0 specifications:

| RFC | Standard | Description |
|-----|----------|-------------|
| [RFC 8693](https://datatracker.ietf.org/doc/html/rfc8693) | Token Exchange | Exchange tokens for different audiences, scopes, or impersonation |
| [RFC 7591](https://datatracker.ietf.org/doc/html/rfc7591) | Dynamic Client Registration | Register clients programmatically with authorization servers |
| [RFC 8414](https://datatracker.ietf.org/doc/html/rfc8414) | Authorization Server Metadata | Discover server endpoints and capabilities automatically |
| [RFC 6750](https://datatracker.ietf.org/doc/html/rfc6750) | Bearer Token Usage | Standard format for OAuth 2.0 access tokens |
| [RFC 7636](https://datatracker.ietf.org/doc/html/rfc7636) | PKCE | Security extension for public clients |
| [RFC 7662](https://datatracker.ietf.org/doc/html/rfc7662) | Token Introspection | Validate and inspect token metadata |
| [RFC 7009](https://datatracker.ietf.org/doc/html/rfc7009) | Token Revocation | Invalidate access and refresh tokens |
| [RFC 9126](https://datatracker.ietf.org/doc/html/rfc9126) | Pushed Authorization Requests | Enhanced authorization request security |

## Configuration

### Client Initialization

Both `Client` and `AsyncClient` accept the same initialization parameters:

```python
from keycardai.oauth import Client, AsyncClient, BasicAuth, Endpoints, ClientConfig

# Minimal initialization
client = Client("https://oauth.example.com")

# Full initialization with all options
client = Client(
    base_url="https://oauth.example.com",
    auth=BasicAuth("client_id", "client_secret"),
    endpoints=Endpoints(
        token="/oauth2/token",
        register="/oauth2/register"
    ),
    config=ClientConfig(
        timeout=60.0,
        max_retries=5
    )
)
```

### ClientConfig Options

Configure client behavior with `ClientConfig`:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `timeout` | `float` | `30.0` | HTTP request timeout in seconds |
| `max_retries` | `int` | `3` | Maximum retry attempts for failed requests |
| `verify_ssl` | `bool` | `True` | Verify SSL/TLS certificates |
| `user_agent` | `str` | `"Keycard-OAuth/0.0.1"` | HTTP User-Agent header |
| `custom_headers` | `dict[str, str] \| None` | `None` | Additional HTTP headers for all requests |
| `enable_metadata_discovery` | `bool` | `True` | Auto-discover server endpoints via RFC 8414 |
| `auto_register_client` | `bool` | `False` | Automatically register client on context entry |
| `client_id` | `str \| None` | `None` | Pre-existing client ID (skip registration) |
| `client_name` | `str` | `"Keycard OAuth Client"` | Client name for dynamic registration |
| `client_redirect_uris` | `list[str]` | `["http://localhost:8080/callback"]` | Redirect URIs for registration |
| `client_grant_types` | `list[GrantType]` | `[AUTHORIZATION_CODE, REFRESH_TOKEN, TOKEN_EXCHANGE]` | Grant types for registration |
| `client_token_endpoint_auth_method` | `TokenEndpointAuthMethod` | `NONE` | Token endpoint auth method |
| `client_jwks_url` | `str \| None` | `None` | JWKS URL for private_key_jwt auth |

Example with custom configuration:

```python
from keycardai.oauth import Client, ClientConfig, GrantType

config = ClientConfig(
    timeout=60.0,
    max_retries=5,
    enable_metadata_discovery=True,
    auto_register_client=True,
    client_name="My Application",
    client_grant_types=[GrantType.TOKEN_EXCHANGE, GrantType.CLIENT_CREDENTIALS]
)

with Client("https://oauth.example.com", config=config) as client:
    # Client automatically discovers endpoints and registers if needed
    response = client.exchange_token(...)
```

### Endpoints Configuration

Override discovered or default endpoints with `Endpoints`:

| Endpoint | RFC | Description |
|----------|-----|-------------|
| `token` | RFC 6749 | Token endpoint for exchanges and grants |
| `introspect` | RFC 7662 | Token introspection endpoint |
| `revoke` | RFC 7009 | Token revocation endpoint |
| `register` | RFC 7591 | Dynamic client registration endpoint |
| `par` | RFC 9126 | Pushed authorization request endpoint |
| `authorize` | RFC 6749 | Authorization endpoint |

```python
from keycardai.oauth import Client, Endpoints

endpoints = Endpoints(
    token="/custom/token",
    register="/custom/register"
)

with Client("https://oauth.example.com", endpoints=endpoints) as client:
    # Uses custom endpoints instead of discovered ones
    pass
```

### Configuration Precedence

Endpoint resolution follows this priority (highest to lowest):

1. **Explicit `Endpoints` overrides** - Always used if provided
2. **Discovered server metadata** - From RFC 8414 discovery (if `enable_metadata_discovery=True`)
3. **Default endpoints** - Standard OAuth 2.0 paths (e.g., `/oauth2/token`)

## Authentication Strategies

The SDK provides four authentication strategies for different use cases.

### NoneAuth

No authentication. Use for public endpoints or dynamic client registration:

```python
from keycardai.oauth import Client, NoneAuth

# For server metadata discovery (no auth required)
with Client("https://oauth.example.com", auth=NoneAuth()) as client:
    metadata = client.discover_server_metadata()
    print(f"Token endpoint: {metadata.token_endpoint}")
```

### BasicAuth (RFC 7617)

HTTP Basic authentication using client credentials:

```python
from keycardai.oauth import Client, BasicAuth

auth = BasicAuth(
    client_id="your_client_id",
    client_secret="your_client_secret"
)

with Client("https://oauth.example.com", auth=auth) as client:
    response = client.exchange_token(
        subject_token="user_token",
        subject_token_type=TokenType.ACCESS_TOKEN,
        audience="https://api.example.com"
    )
```

### BearerAuth (RFC 6750)

Bearer token authentication for API access:

```python
from keycardai.oauth import Client, BearerAuth

# Use an existing access token for authentication
auth = BearerAuth(access_token="your_access_token")

with Client("https://oauth.example.com", auth=auth) as client:
    response = client.exchange_token(
        subject_token="another_token",
        subject_token_type=TokenType.ACCESS_TOKEN,
        resource="https://api.example.com"
    )
```

### MultiZoneBasicAuth

For multi-zone deployments with different credentials per zone:

```python
from keycardai.oauth import MultiZoneBasicAuth

# Configure credentials for multiple zones
auth = MultiZoneBasicAuth({
    "production": ("prod_client_id", "prod_client_secret"),
    "staging": ("staging_client_id", "staging_client_secret"),
    "development": ("dev_client_id", "dev_client_secret"),
})

# Check available zones
print(auth.get_configured_zones())  # ['production', 'staging', 'development']

# Check if a zone exists
if auth.has_zone("production"):
    # Get headers for a specific zone
    headers = auth.get_headers_for_zone("production")

    # Or get the BasicAuth instance for a zone
    prod_auth = auth.get_auth_for_zone("production")
```

## Operations

### Token Exchange (RFC 8693)

Exchange tokens for different audiences, scopes, or perform delegation/impersonation:

```python
from keycardai.oauth import Client, BasicAuth, TokenType, TokenExchangeRequest

with Client("https://oauth.example.com", auth=BasicAuth(...)) as client:
    # Simple delegation - exchange for a different audience
    response = client.exchange_token(
        subject_token="user_access_token",
        subject_token_type=TokenType.ACCESS_TOKEN,
        audience="https://api.example.com"
    )
    print(f"Delegated token: {response.access_token}")

    # Exchange with scope restriction
    response = client.exchange_token(
        subject_token="user_access_token",
        subject_token_type=TokenType.ACCESS_TOKEN,
        audience="https://api.example.com",
        scope="read:users"
    )

    # Advanced: Impersonation with actor token
    request = TokenExchangeRequest(
        subject_token="user_token",
        subject_token_type=TokenType.ACCESS_TOKEN,
        actor_token="service_account_token",
        actor_token_type=TokenType.ACCESS_TOKEN,
        audience="https://backend-api.example.com"
    )
    response = client.exchange_token(request)
```

### Dynamic Client Registration (RFC 7591)

Register OAuth clients programmatically:

```python
from keycardai.oauth import Client, ClientRegistrationRequest, GrantType, TokenEndpointAuthMethod

with Client("https://oauth.example.com") as client:
    # Simple registration with defaults
    response = client.register_client(client_name="My Application")
    print(f"Client ID: {response.client_id}")
    print(f"Client Secret: {response.client_secret}")

    # Full control over registration
    request = ClientRegistrationRequest(
        client_name="Production Web App",
        redirect_uris=[
            "https://app.example.com/callback",
            "https://app.example.com/silent-refresh"
        ],
        grant_types=[
            GrantType.AUTHORIZATION_CODE,
            GrantType.REFRESH_TOKEN,
            GrantType.TOKEN_EXCHANGE
        ],
        token_endpoint_auth_method=TokenEndpointAuthMethod.CLIENT_SECRET_BASIC,
        scope="openid profile email"
    )
    response = client.register_client(request)
```

### Server Metadata Discovery (RFC 8414)

Discover authorization server capabilities:

```python
from keycardai.oauth import Client

with Client("https://oauth.example.com") as client:
    metadata = client.discover_server_metadata()

    print(f"Issuer: {metadata.issuer}")
    print(f"Token endpoint: {metadata.token_endpoint}")
    print(f"Registration endpoint: {metadata.registration_endpoint}")
    print(f"Supported grants: {metadata.grant_types_supported}")
    print(f"Supported scopes: {metadata.scopes_supported}")
    print(f"PKCE methods: {metadata.code_challenge_methods_supported}")
```

## Error Handling

The SDK provides a structured exception hierarchy with retry guidance.

### Exception Hierarchy

```
OAuthError (base)
├── OAuthHttpError          # HTTP 4xx/5xx responses
├── OAuthProtocolError      # RFC 6749 OAuth error responses
│   └── TokenExchangeError  # RFC 8693 specific errors
├── NetworkError            # Connection/transport failures
├── ConfigError             # Client misconfiguration
└── AuthenticationError     # Authentication failures
```

### Retriable vs Non-Retriable Errors

| Exception | Retriable | Condition |
|-----------|-----------|-----------|
| `OAuthHttpError` | Yes | HTTP 429 (rate limit) or 5xx (server error) |
| `OAuthHttpError` | No | HTTP 4xx (client error, except 429) |
| `OAuthProtocolError` | No | OAuth protocol violations |
| `TokenExchangeError` | No | Token exchange failures |
| `NetworkError` | Yes | Connection timeouts, DNS failures |
| `ConfigError` | No | Invalid configuration (requires code fix) |
| `AuthenticationError` | No | Invalid credentials |

### Error Handling Patterns

```python
from keycardai.oauth import (
    Client,
    BasicAuth,
    OAuthError,
    OAuthHttpError,
    OAuthProtocolError,
    NetworkError,
    ConfigError,
    AuthenticationError,
)

with Client("https://oauth.example.com", auth=BasicAuth(...)) as client:
    try:
        response = client.exchange_token(
            subject_token="token",
            subject_token_type=TokenType.ACCESS_TOKEN,
            audience="https://api.example.com"
        )
    except OAuthHttpError as e:
        if e.retriable:
            # HTTP 429 or 5xx - implement backoff and retry
            print(f"Retriable HTTP error (status {e.status_code}): {e}")
        else:
            # HTTP 4xx - fix the request
            print(f"Client error: {e.response_body}")

    except OAuthProtocolError as e:
        # OAuth error response from server
        print(f"OAuth error: {e.error}")
        print(f"Description: {e.error_description}")
        if e.error_uri:
            print(f"More info: {e.error_uri}")

    except NetworkError as e:
        # Connection issues - usually retriable
        print(f"Network error (retriable: {e.retriable}): {e.cause}")

    except ConfigError as e:
        # Configuration issue - fix code
        print(f"Configuration error: {e}")

    except AuthenticationError as e:
        # Credentials invalid
        print(f"Authentication failed: {e}")
```

### Implementing Retry Logic

```python
import time
from keycardai.oauth import Client, BasicAuth, OAuthHttpError, NetworkError

def exchange_with_retry(client, max_attempts=3, base_delay=1.0):
    """Exchange token with exponential backoff for retriable errors."""
    for attempt in range(max_attempts):
        try:
            return client.exchange_token(
                subject_token="token",
                subject_token_type=TokenType.ACCESS_TOKEN,
                audience="https://api.example.com"
            )
        except (OAuthHttpError, NetworkError) as e:
            if not e.retriable or attempt == max_attempts - 1:
                raise
            delay = base_delay * (2 ** attempt)
            print(f"Attempt {attempt + 1} failed, retrying in {delay}s...")
            time.sleep(delay)
```

## Utility Functions

### Bearer Token Utilities

Extract and validate bearer tokens from HTTP headers:

```python
from keycardai.oauth import extract_bearer_token, validate_bearer_format

# Extract token from Authorization header
header = "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
token = extract_bearer_token(header)
print(token)  # "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."

# Validate token format
is_valid = validate_bearer_format(token)
print(f"Token format valid: {is_valid}")
```

## Examples

Working examples are available in the `examples/` directory:

- **[discover_server_metadata](examples/discover_server_metadata/)** - RFC 8414 server metadata discovery
- **[dynamic_client_registration](examples/dynamic_client_registration/)** - RFC 7591 client registration

Run examples:

```bash
cd examples/discover_server_metadata
ZONE_URL="https://your-zone.keycard.cloud" uv run python main.py
```

## API Reference

> **Note**: Auto-generated API documentation is planned for a future release.
> For now, refer to the inline docstrings in the source code and the examples
> in this README. The SDK includes comprehensive docstrings with RFC references.

## Development

This package is part of the [Keycard Python SDK workspace](../../README.md).

To develop:

```bash
# From workspace root
uv sync
uv run --package keycardai-oauth pytest
```

Run tests with coverage:

```bash
uv run --package keycardai-oauth pytest --cov=keycardai.oauth --cov-report=term-missing
```

## License

MIT License - see [LICENSE](../../LICENSE) file for details.

## Support

- **Documentation**: [Keycard Docs](https://docs.keycard.ai)
- **Issues**: [GitHub Issues](https://github.com/keycardai/python-sdk/issues)
- **Email**: support@keycard.ai
