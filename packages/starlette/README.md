# keycardai-starlette

Starlette/FastAPI integration for Keycard. Plugs into Starlette's standard
authentication framework: an `AuthenticationBackend` populates `request.user`
and `request.auth`, the `@requires` decorator gates routes, and
`@auth.grant(resource)` performs delegated OAuth 2.0 token exchange.

## Installation

```bash
pip install keycardai-starlette
```

## Quick Start

```python
from fastapi import FastAPI, Request
from keycardai.starlette import AuthProvider, KeycardUser, requires
from keycardai.oauth.server import AccessContext, ClientSecret

auth = AuthProvider(
    zone_id="your-zone-id",
    application_credential=ClientSecret(("client_id", "client_secret")),
)

app = FastAPI()
auth.install(app)  # AuthenticationMiddleware + /.well-known/* routes

@app.get("/health")
async def health():
    return {"ok": True}                    # public, no decorator

@app.get("/api/me")
@requires("authenticated")                 # standard Starlette gating
async def me(request: Request):
    user: KeycardUser = request.user
    return {"client_id": user.client_id, "scopes": list(request.auth.scopes)}

@app.get("/api/data")
@requires("authenticated")
@auth.grant("https://api.example.com")     # delegated token exchange (RFC 8693)
async def get_data(request: Request, access: AccessContext):
    token = access.access("https://api.example.com").access_token
```

## How it integrates with Starlette

`AuthProvider.install(app)` does two things:

1. Adds `starlette.middleware.authentication.AuthenticationMiddleware` wired
   to a `KeycardAuthBackend` so every request gets a populated `request.user`
   and `request.auth`.
2. Mounts the OAuth discovery endpoints under `/.well-known/`.

Routes you do not decorate stay public: the backend returns `None` (anonymous
user) when no `Authorization` header is present, exactly like
`starlette.authentication.UnauthenticatedUser`. Routes that need a verified
caller use the `@requires(...)` decorator.

## Decorators

### `@requires(scopes)`

`keycardai.starlette.requires` is a drop-in for
`starlette.authentication.requires` with one difference: anonymous requests
get an RFC 6750 401 response with a
`WWW-Authenticate: Bearer ... resource_metadata="..."` header (RFC 9728)
instead of stock `HTTPException(403)`. Scope checks behave the same.

```python
@requires("authenticated")              # any verified caller
@requires(["authenticated", "admin"])   # additional scope check
```

`AuthProvider.requires` is exposed as a static-method alias if you prefer
accessing the decorator via the provider instance:

```python
@auth.requires("authenticated")
```

### `@auth.grant(resource)`

Performs OAuth 2.0 delegated token exchange (RFC 8693) for one or more
downstream resources and injects an `AccessContext` parameter into the
endpoint. Mirrors the `@grant()` decorator from `keycardai-mcp` so the
decorator name is consistent across packages.

```python
@app.get("/api/calendar")
@requires("authenticated")
@auth.grant("https://graph.microsoft.com")
async def calendar(request: Request, access: AccessContext):
    token = access.access("https://graph.microsoft.com").access_token
```

Errors from the exchange are stored per-resource on the `AccessContext`
rather than raised: call `access.has_errors()` / `access.get_errors()` to
decide how to respond. The `AccessContext` parameter is hidden from FastAPI
introspection via `__signature__` rewriting, so it never appears in the
generated OpenAPI schema.

## Other entry points

### `protected_router()`

Mount any ASGI app behind Keycard authentication and the `/.well-known/*`
metadata routes in one call. Useful when every route under some prefix needs
the same protection (for example an MCP transport, an internal admin app).

```python
from keycardai.starlette import protected_router
from starlette.applications import Starlette

inner = build_my_api()  # any ASGI app

app = Starlette(routes=protected_router(
    issuer=auth.issuer,
    app=inner,
    verifier=auth.get_token_verifier(),
))
```

### `AuthenticationMiddleware` directly

For full control over middleware ordering, register the standard Starlette
middleware yourself:

```python
from starlette.middleware.authentication import AuthenticationMiddleware
from keycardai.starlette import KeycardAuthBackend, keycard_on_error

app.add_middleware(
    AuthenticationMiddleware,
    backend=KeycardAuthBackend(auth.get_token_verifier()),
    on_error=keycard_on_error,
)
```

## What `install()` adds

- `AuthenticationMiddleware` with `KeycardAuthBackend`
- `/.well-known/oauth-protected-resource` (RFC 9728)
- `/.well-known/oauth-authorization-server` (RFC 8414)
- `/.well-known/jwks.json` (only when `WebIdentity` is configured)

The middleware never gates access on its own; it just populates
`request.user` / `request.auth`. Routes you do not decorate stay public.
