# KeyCard AI OAuth SDK

A comprehensive Python SDK for OAuth 2.0 functionality implementing multiple OAuth 2.0 standards for enterprise-grade token management.

## Installation

```bash
pip install keycardai-oauth
```

## Quick Start

```python
from keycardai.oauth import *

# Token Exchange (RFC 8693)
exchange_client = TokenExchangeClient("https://oauth.example.com/token")
response = await exchange_client.exchange_token(
    subject_token="original_token",
    subject_token_type=TokenTypes.ACCESS_TOKEN,
    resource="https://api.example.com"
)

# Token Introspection (RFC 7662)
introspection_client = IntrospectionClient(
    "https://auth.example.com/introspect", 
    "client_id", 
    "client_secret"
)
token_info = await introspection_client.introspect_token("token_to_check")

# Token Revocation (RFC 7009)
revocation_client = RevocationClient(
    "https://auth.example.com/revoke",
    "client_id",
    "client_secret" 
)
await revocation_client.revoke_token("token_to_revoke")
```

## 🏗️ Architecture & Standards

This SDK implements a comprehensive set of OAuth 2.0 standards:

### Core Token Operations

| Standard | Module | Description |
|----------|---------|-------------|
| **[RFC 8693](https://datatracker.ietf.org/doc/html/rfc8693)** | `exchange.py` | **OAuth 2.0 Token Exchange** - Delegation and impersonation through standardized token exchange |
| **[RFC 7662](https://datatracker.ietf.org/doc/html/rfc7662)** | `introspection.py` | **Token Introspection** - Validate tokens and retrieve metadata |
| **[RFC 7009](https://datatracker.ietf.org/doc/html/rfc7009)** | `revocation.py` | **Token Revocation** - Invalidate access and refresh tokens |

### Authentication & Security

| Standard | Module | Description |
|----------|---------|-------------|
| **[RFC 7523](https://datatracker.ietf.org/doc/html/rfc7523)** | `jwt_profile.py` | **JWT Client Authentication** - Private key JWT client authentication |
| **[RFC 9068](https://datatracker.ietf.org/doc/html/rfc9068)** | `jwt_profile.py` | **JWT Access Tokens** - Structured JWT access tokens |
| **[RFC 6750](https://datatracker.ietf.org/doc/html/rfc6750)** | `bearer.py` | **Bearer Token Usage** - HTTP Bearer token authentication |
| **[RFC 8705](https://datatracker.ietf.org/doc/html/rfc8705)** | `security.py` | **Mutual TLS** - Certificate-bound tokens and client authentication |

### Discovery & Extensions

| Standard | Module | Description |
|----------|---------|-------------|
| **[RFC 8414](https://datatracker.ietf.org/doc/html/rfc8414)** | `discovery.py` | **Authorization Server Metadata** - Discover OAuth endpoints and capabilities |
| **[RFC 7636](https://datatracker.ietf.org/doc/html/rfc7636)** | `security.py` | **PKCE** - Proof Key for Code Exchange for public clients |
| **[RFC 9126](https://datatracker.ietf.org/doc/html/rfc9126)** | `security.py` | **Pushed Authorization Requests** - Enhanced security for authorization requests |

## Features

- ✅ **Comprehensive RFC Implementation**: Full implementation of 9+ OAuth 2.0 RFCs
- ✅ **Type Safe**: Full type hints with Pydantic models
- ✅ **Async Support**: Native async/await support for all operations
- ✅ **Enterprise Ready**: Mutual TLS, certificate binding, and advanced security features
- ✅ **Extensible**: Pluggable authentication and validation components
- ✅ **Well Tested**: Comprehensive test suite with >90% coverage

## Development

This package is part of the [KeycardAI Python SDK workspace](../../README.md). 

To develop:

```bash
# From workspace root
uv sync
uv run --package keycardai-oauth pytest
```

## License

MIT License - see [LICENSE](../../LICENSE) file for details.
