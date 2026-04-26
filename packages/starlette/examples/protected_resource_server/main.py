"""Protected resource server example using keycardai-starlette.

Demonstrates the standard Starlette authentication flow:

- ``AuthProvider.install(app)`` installs ``AuthenticationMiddleware`` plus the
  RFC 9728 / RFC 8414 ``/.well-known/*`` discovery endpoints.
- ``@requires("authenticated")`` (Keycard's drop-in for
  ``starlette.authentication.requires``) gates routes and emits an RFC 6750
  ``WWW-Authenticate`` challenge for anonymous requests.
- ``@auth.grant(resource)`` performs delegated token exchange (RFC 8693) and
  injects an ``AccessContext`` for downstream API calls.
"""

import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request

from keycardai.oauth.server import AccessContext, ClientSecret
from keycardai.starlette import AuthProvider, KeycardUser, requires

load_dotenv()

ZONE_URL = os.environ["KEYCARD_ZONE_URL"]
CLIENT_ID = os.environ["KEYCARD_CLIENT_ID"]
CLIENT_SECRET = os.environ["KEYCARD_CLIENT_SECRET"]
DOWNSTREAM_RESOURCE = os.environ["DOWNSTREAM_RESOURCE"]
# Resource indicator (RFC 8707) Keycard mints tokens for. The verifier
# rejects tokens whose ``aud`` claim does not include this value.
RESOURCE_AUDIENCE = "http://localhost:7878/api"

auth = AuthProvider(
    zone_url=ZONE_URL,
    application_credential=ClientSecret((CLIENT_ID, CLIENT_SECRET)),
    audience=RESOURCE_AUDIENCE,
)

app = FastAPI()
auth.install(app)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/me")
@requires("authenticated")
async def me(request: Request):
    user: KeycardUser = request.user
    return {
        "client_id": user.client_id,
        "zone_id": user.zone_id,
        "scopes": list(request.auth.scopes),
    }


@app.get("/api/events")
@requires("authenticated")
@auth.grant(DOWNSTREAM_RESOURCE)
async def events(request: Request, access: AccessContext):
    if access.has_errors():
        return {"error": access.get_errors()}
    token_response = access.access(DOWNSTREAM_RESOURCE)
    return {
        "downstream_token_type": token_response.token_type,
        "downstream_expires_in": token_response.expires_in,
    }


def main():
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=7878)


if __name__ == "__main__":
    main()
