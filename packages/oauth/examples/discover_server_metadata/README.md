# Discover Server Metadata Example

Demonstrates OAuth 2.0 Authorization Server Metadata discovery (RFC 8414) using the Keycard OAuth client.

## What it does

Connects to a Keycard zone and retrieves its OAuth server metadata, which includes:
- Authorization endpoint
- Token endpoint
- Supported grant types, scopes, and response types
- Registration endpoint (if dynamic client registration is enabled)

## Usage

### Environment variable:
```bash
export ZONE_URL=http://kq0sohre3tpcywxjnog16iipay.localdev.keycard.sh
uv run python main.py
```

### Install and run:
```bash
uv sync
export ZONE_URL=http://kq0sohre3tpcywxjnog16iipay.localdev.keycard.sh
uv run python main.py
```

## Requirements

- Python 3.10+
- keycardai-oauth package
- Access to a Keycard authorization server
