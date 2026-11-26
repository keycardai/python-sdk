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
    # Resource indicator (RFC 8707) Keycard mints tokens for. The verifier
    # rejects tokens whose "aud" claim does not include this value.
    resource_identifier="https://your-api.example.com/api/me",
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

# Each discoverable route needs its own binding; /api/data has no discovery.
@app.get("/api/data")
@requires("authenticated")
@auth.grant("https://api.example.com")     # delegated token exchange (RFC 8693)
async def get_data(request: Request, access: AccessContext):
    token = access.access("https://api.example.com").access_token
```

## Resource binding and OAuth discovery

Choose one mode when constructing `AuthProvider`:

| Options | Audience and discovery |
|---------|------------------------|
| `resource_identifier="https://api.example.com/mcp"` | Accepts that exact audience and publishes RFC 9728 metadata for that URL. A URN such as `urn:example:service` binds the audience without publishing metadata. |
| `resource_identifiers_by_zone={"a": "https://api.example.com/a/mcp", ...}`, `enable_multi_zone=True` | Selects the zone before verification from a `{zone_id}` routing parameter. `protected_router` mounts the app at `/{zone_id}`; with `install`, declare the parameter in your routes. A missing or unconfigured zone is rejected. Each HTTP(S) identifier has its own document. |
| `multi_resource=True`, `audience=["resource-a", "resource-b"]` | Accepts any listed audience without publishing metadata. |
| `multi_resource=True`, `resource_identifiers_by_zone={...}`, `audience=[shared_url]` | Publishes one document when all trusted zones share the exact same HTTP(S) identifier. Zone issuers are derived from `base_url` (default: `https://keycard.cloud`), and `auth.issuer` is that base URL. Leave `enable_multi_zone` unset or `False` so the token selects its issuer, including with a multi-zone `ClientSecret`. |
| `allow_any_audience=True` | Skips only the audience check, without publishing metadata. |

Leave the binding options unset to retain legacy audience and discovery
behavior during deprecation. This emits one `LegacyResourceBindingWarning`
per configuration. Legacy support will not be removed until at least 60
days after the first minor release that emits this warning.

For an HTTP(S) identifier like `https://api.example.com/app/mcp`, publish
metadata at the public host root:
`https://api.example.com/.well-known/oauth-protected-resource/app/mcp`.
If your app is mounted under `/app`, register the metadata routes on the
outer app or configure your proxy to forward from that public URL to the
metadata handler. This registration can reuse the provider's binding:

```python
from keycardai.starlette import well_known_metadata_routes

root.router.routes.extend(
    well_known_metadata_routes(auth.issuer, resource_binding=auth.resource_binding)
)
```

An explicit challenge advertises that URL only when the original request
path and query match the configured identifier, regardless of the internal
host, scheme, or forwarded headers. Give each independently discoverable
route its own binding.

## Configuration from the environment

`AuthProvider` reads its zone and application credential from the environment
when they are not passed explicitly.

Zone: set `KEYCARD_ZONE_URL` to the full zone URL. `KEYCARD_ZONE_ID` and
`KEYCARD_BASE_URL` still work but emit a `DeprecationWarning`; migrate as
follows:

| Deprecated | Canonical replacement |
|------------|-----------------------|
| `KEYCARD_ZONE_ID="abc1234"` | `KEYCARD_ZONE_URL="https://abc1234.keycard.cloud"` |
| `KEYCARD_ZONE_ID="abc1234"` + `KEYCARD_BASE_URL="https://custom.example.com"` | `KEYCARD_ZONE_URL="https://abc1234.custom.example.com"` |

Application credential: discovery is delegated to
`keycardai.oauth.server.discover_credential`. `KEYCARD_APPLICATION_CREDENTIAL_TYPE`
selects `client_secret`, `workload_identity` or `web_identity`
(`eks_workload_identity` is accepted as a legacy alias). Without a selector,
exactly one source may be configured: `KEYCARD_CLIENT_ID` +
`KEYCARD_CLIENT_SECRET`, an injected workload token file
(`AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE`, `AWS_WEB_IDENTITY_TOKEN_FILE`,
`AZURE_FEDERATED_TOKEN_FILE` or `KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE`), or
`KEYCARD_WEB_IDENTITY_KEY_STORAGE_DIR`. More than one source with no selector
fails at startup with an ambiguity error naming the selector as the remedy.

## How it integrates with Starlette

`AuthProvider.install(app)` does two things:

1. Adds `starlette.middleware.authentication.AuthenticationMiddleware` wired
   to a `KeycardAuthBackend` so every request gets a populated `request.user`
   and `request.auth`.
2. Mounts the OAuth discovery endpoints under `/.well-known/`.

Outside per-zone mode, routes you do not decorate stay public: the backend
returns `None` (anonymous user) when no `Authorization` header is present, like
`starlette.authentication.UnauthenticatedUser`. Routes that need a verified
caller use the `@requires(...)` decorator.

## Decorators

### `@requires(scopes)`

`keycardai.starlette.requires` is a drop-in for
`starlette.authentication.requires` with one difference: anonymous requests
get an RFC 6750 401 response with a
`WWW-Authenticate: Bearer ...` header (with `resource_metadata` when applicable)
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
    resource_binding=auth.resource_binding,
))
```

#### Opaque sub-apps: `require_authentication=True`

`@requires(...)` only gates routes you decorate. A mounted sub-app that
handles its own routing (an MCP JSONRPC dispatcher, a gRPC handler, any
non-Starlette ASGI app) bypasses route decorators, so anonymous requests
would fall through to it. Pass `require_authentication=True` to make the
backend itself the gate: requests without an `Authorization` header get an
RFC 6750 401 challenge instead of reaching the sub-app anonymously.

```python
app = Starlette(routes=protected_router(
    issuer=auth.issuer,
    app=inner,
    verifier=auth.get_token_verifier(),
    resource_binding=auth.resource_binding,
    require_authentication=True,  # every request to `inner` needs a token
))
```

The same flag exists on `KeycardAuthBackend(verifier, require_authentication=True)`
when you register the middleware yourself. OAuth metadata paths under
`/.well-known/` stay public either way (RFC 9728 §2, RFC 8414 §3).

### `AuthenticationMiddleware` directly

For full control over middleware ordering, register the standard Starlette
middleware yourself:

```python
from starlette.middleware.authentication import AuthenticationMiddleware
from keycardai.starlette import KeycardAuthBackend, keycard_on_error

app.add_middleware(
    AuthenticationMiddleware,
    backend=KeycardAuthBackend(
        auth.get_token_verifier(), resource_binding=auth.resource_binding
    ),
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
