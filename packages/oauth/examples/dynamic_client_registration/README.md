# Dynamic Client Registration Example with Server Metadata

Demonstration of OAuth 2.0 Dynamic Client Registration (RFC 7591) using real KeyCard server metadata and endpoint configuration.

## What it does

Registers a new OAuth client with:
- Random client name: `mcp-client-{8-char-hex}`
- Token endpoint auth method: `none` (public client)
- Grant types: `authorization_code`, `refresh_token` (for user authentication flows)
- Response types: `code` (authorization code flow)
- Redirect URI: `http://localhost:8080/callback` (for development)
- Proper endpoint configuration based on server metadata
- No authentication required for registration

## Features

- **Real server metadata**: Uses actual KeyCard authorization server endpoints
- **Endpoint configuration**: Demonstrates how to configure custom endpoints
- **Comprehensive output**: Shows all registration details including access tokens and URIs
- **Error handling**: Graceful handling of registration failures

## Usage

### Command line argument:
```bash
python main.py http://kq0sohre3tpcywxjnog16iipay.localdev.keycard.sh
```

### Environment variable:
```bash
export ZONE_URL=http://kq0sohre3tpcywxjnog16iipay.localdev.keycard.sh
python main.py
```

### Install and run:
```bash
uv sync
uv run python main.py http://kq0sohre3tpcywxjnog16iipay.localdev.keycard.sh
```

## Example output

```
Registering client: mcp-client-a1b2c3d4
Base URL: http://kq0sohre3tpcywxjnog16iipay.localdev.keycard.sh
Registration endpoint: http://api.localdev.keycard.sh/oauth/2/0nne96kqicguomp2gj9brucr8o/kq0sohre3tpcywxjnog16iipay/clients

✅ Registration successful!
Client ID: abc123def456
Client Name: mcp-client-a1b2c3d4
No client secret (public client)
Registration Access Token: ey...
Registration Client URI: http://api.localdev.keycard.sh/oauth/2/clients/abc123def456
Client Secret: Never expires
```

## Requirements

- Python 3.10+
- keycardai-oauth package
- Access to a KeyCard authorization server