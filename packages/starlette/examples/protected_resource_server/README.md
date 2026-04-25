# Protected Resource Server Example

Demonstrates how to protect a FastAPI/Starlette application with Keycard
using the standard Starlette authentication framework: an
`AuthenticationBackend` populates `request.user` / `request.auth`, the
`@requires` decorator gates routes, and `@auth.grant(resource)` performs
delegated OAuth 2.0 token exchange (RFC 8693) for downstream API calls.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- A Keycard zone with a confidential application credential

## Configuration

```bash
cp .env.example .env
# fill in KEYCARD_CLIENT_ID and KEYCARD_CLIENT_SECRET in .env
```

## Run

```bash
uv sync
uv run protected-resource-server
```

The server starts on `http://127.0.0.1:7878`. The `/api/*` paths are the
protected resource URLs that Keycard issues access tokens for (see
`keycard.toml`).

## Endpoints

| Path           | Auth                                  | Purpose                              |
| -------------- | ------------------------------------- | ------------------------------------ |
| `/health`      | public                                | liveness                             |
| `/api/me`      | `@requires("authenticated")`          | echoes the verified token's claims   |
| `/api/events`  | `@requires` + `@auth.grant(resource)` | delegated exchange for `$DOWNSTREAM_RESOURCE` |

`auth.install(app)` also adds the OAuth discovery endpoints:

- `/.well-known/oauth-protected-resource` (RFC 9728)
- `/.well-known/oauth-authorization-server` (RFC 8414)

## Smoke tests

```bash
# 1. Public route
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7878/health

# 2. Discovery
curl -s http://127.0.0.1:7878/.well-known/oauth-protected-resource | jq .

# 3. Anonymous protected route -> 401 with WWW-Authenticate challenge
curl -i http://127.0.0.1:7878/api/me

# 4. Bad token -> 401
curl -i -H "Authorization: Bearer not-a-real-token" http://127.0.0.1:7878/api/me

# 5. Valid token via Keycard CLI -> 200
keycard auth signin    # one-time, opens a browser
ACCESS_TOKEN=$(keycard credential read http://localhost:7878/api)
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" http://127.0.0.1:7878/api/me
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" http://127.0.0.1:7878/api/events
```
