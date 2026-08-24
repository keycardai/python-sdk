# Web-App Authorization Code Flow Example

Demonstrates the stateless web-app authorization-code flow with PKCE. A web
application stores `state` and `code_verifier` in session state between its
login and callback routes, then passes them to `complete_authorization`.

The example is framework-agnostic. Connect `login_redirect` and
`oauth_callback` to routes in your web framework and replace the in-memory
`session` mapping with the framework's session storage.

## Usage

```bash
uv sync
uv run python main.py
```

Replace the example issuer, client ID, and redirect URI with values registered
with your authorization server before connecting the handlers to routes.

## Requirements

- Python 3.10+
- keycardai-oauth package
- Access to a Keycard authorization server
