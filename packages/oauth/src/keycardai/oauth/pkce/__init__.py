"""High-level PKCE flows for browser-based OAuth 2.0 user authentication.

Builds on the lower-level PKCE primitives in :mod:`keycardai.oauth.utils.pkce`
and reuses :class:`keycardai.oauth.AsyncClient` for the OAuth-server-facing
operations (server metadata discovery, code exchange). This package owns the
user-flow orchestration on top: issuer resolution (directly via ``issuer``
or from a ``WWW-Authenticate`` challenge per RFC 9728), local callback
server (RFC 8252), and browser launch.

Example (challenge-driven)::

    from keycardai.oauth.pkce import authenticate

    token = await authenticate(
        client_id="my-app",
        resource_url="https://api.example.com",
        www_authenticate_header=resp.headers["WWW-Authenticate"],
    )
    print(token.access_token)

Example (issuer-direct)::

    token = await authenticate(
        client_id="my-app",
        issuer="https://auth.example.com",
    )

Example (web app)::

    redirect = await begin_authorization(
        client_id="my-app",
        issuer="https://auth.example.com",
        redirect_uri="https://app.example.com/oauth/callback",
        resources=["https://api.example.com", "https://files.example.com"],
    )
    session["oauth_flow"] = {
        "state": redirect.state,
        "code_verifier": redirect.code_verifier,
        "resources": redirect.resources,
    }
    # Redirect the browser to ``redirect.url``. In the callback route:
    flow = session.pop("oauth_flow")
    token = await complete_authorization(
        callback_params=request.query_params,
        state=flow["state"],
        code_verifier=flow["code_verifier"],
        client_id="my-app",
        issuer="https://auth.example.com",
        redirect_uri="https://app.example.com/oauth/callback",
    )
"""

from ._issuer import resolve_issuer_from_challenge
from .callback import OAuthCallbackServer
from .client import authenticate
from .web import AuthorizationRedirect, begin_authorization, complete_authorization

__all__ = [
    "AuthorizationRedirect",
    "OAuthCallbackServer",
    "authenticate",
    "begin_authorization",
    "complete_authorization",
    "resolve_issuer_from_challenge",
]
