# keycardai-starlette

Starlette/FastAPI middleware, route builders, and an `@protect()` decorator for
protecting HTTP APIs with Keycard OAuth.

## Installation

```bash
pip install keycardai-starlette
```

## Quick Start

```python
from fastapi import FastAPI, Request
from keycardai.starlette import AuthProvider
from keycardai.oauth.server import AccessContext, ClientSecret

auth = AuthProvider(
    zone_id="your-zone-id",
    application_credential=ClientSecret(("client_id", "client_secret")),
)

app = FastAPI()
auth.install(app)  # adds /.well-known/* metadata routes only

@app.get("/health")
async def health():
    return {"ok": True}                # public, no auth

@app.get("/api/me")
@auth.protect()                        # verify only
async def me(request: Request):
    return request.state.keycardai_auth_info

@app.get("/api/data")
@auth.protect("https://api.example.com")  # verify + delegated exchange
async def get_data(request: Request, access: AccessContext):
    token = access.access("https://api.example.com").access_token
    # Use token to call downstream API
```

## Protecting routes

Three patterns, choose the one that fits your service:

### `@auth.protect()` per route

Routes are public by default after `auth.install(app)`. Add the decorator to
each route that needs a verified bearer token. Use the form with no arguments
when you only need to authenticate the caller, and the form with a resource
URL when you also need a delegated downstream token.

```python
@app.get("/api/me")
@auth.protect()
async def me(request: Request):
    ...

@app.get("/api/calendar")
@auth.protect("https://graph.microsoft.com")
async def calendar(request: Request, access: AccessContext):
    token = access.access("https://graph.microsoft.com").access_token
    ...
```

### `protected_router()` for whole subtrees

Mount an entire ASGI app behind bearer auth. Useful when every route under
some prefix needs the same protection (e.g. an MCP transport, an internal
admin app).

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

### `BearerAuthMiddleware` directly

Use this when you want full control over middleware ordering or want to
attach the middleware to a specific Mount yourself.

```python
from keycardai.starlette import BearerAuthMiddleware

app.add_middleware(BearerAuthMiddleware, verifier=auth.get_token_verifier())
```

## What `install()` adds

- `/.well-known/oauth-protected-resource` (RFC 9728)
- `/.well-known/oauth-authorization-server` (RFC 8414)
- `/.well-known/jwks.json` (only when `WebIdentity` is configured)

`install()` does not add bearer auth middleware globally. Routes you do not
decorate stay public.
