# KeyCard AI OAuth SDK

A comprehensive Python SDK for OAuth 2.0 functionality implementing multiple OAuth 2.0 standards for enterprise-grade token management.

## Installation

```bash
uv add keycardai-oauth
```

## Quick Start

```python
from keycardai.oauth import Client

with Client("https://oauth.example.com/token") as client:
    response = await client.exchange_token(
        subject_token="original_token",
        subject_token_type=TokenTypes.ACCESS_TOKEN,
        resource="https://api.example.com"
    )

```

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
