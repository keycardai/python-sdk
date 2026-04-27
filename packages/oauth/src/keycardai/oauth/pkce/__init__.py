"""High-level PKCE flow for browser-based OAuth 2.0 user authentication.

Builds on the lower-level PKCE primitives in :mod:`keycardai.oauth.utils.pkce`
to drive the full authorization code with PKCE flow: discovery from a
``WWW-Authenticate`` challenge (RFC 9728), local callback server, browser
authorization, and token exchange.

Example::

    from keycardai.oauth.pkce import PKCEClient

    async with PKCEClient(client_id="my-app") as pkce:
        token = await pkce.authenticate(
            resource_url="https://api.example.com",
            www_authenticate_header=resp.headers["WWW-Authenticate"],
        )
        print(token.access_token)
"""

from .callback import OAuthCallbackServer
from .client import PKCEClient

__all__ = ["OAuthCallbackServer", "PKCEClient"]
