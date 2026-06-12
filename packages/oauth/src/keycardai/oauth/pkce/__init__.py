"""High-level PKCE flow for browser-based OAuth 2.0 user authentication.

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
"""

from .callback import OAuthCallbackServer
from .client import authenticate, resolve_issuer_from_challenge

__all__ = ["OAuthCallbackServer", "authenticate", "resolve_issuer_from_challenge"]
