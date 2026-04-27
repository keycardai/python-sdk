"""High-level PKCE flow for browser-based OAuth 2.0 user authentication.

The :func:`authenticate` function drives the full authorization code with
PKCE flow:

1. Parse the ``WWW-Authenticate`` challenge from the protected resource
   (RFC 9728)
2. Fetch protected resource metadata, then drive
   :class:`keycardai.oauth.AsyncClient` against the discovered authorization
   server for metadata discovery (RFC 8414) and code exchange (RFC 6749 +
   RFC 7636)
3. Generate a PKCE verifier/challenge pair (RFC 7636) and a CSRF state value
4. Start a local callback server, open the user's browser at the authorize
   endpoint
5. Receive the authorization code from the redirect
6. Exchange the code at the token endpoint and return a
   :class:`~keycardai.oauth.types.models.TokenResponse`

The OAuth-server-facing operations (server metadata discovery, token
exchange) go through :class:`AsyncClient` so there is one client surface in
``keycardai.oauth`` that talks to OAuth servers. This module is the
user-flow orchestration on top: it parses the RFC 9728 challenge, opens the
browser, and runs the loopback callback server (RFC 8252).
"""

import logging
import re
import secrets
import webbrowser
from typing import Any

import httpx

from ..client import AsyncClient
from ..http.auth import BasicAuth, NoneAuth
from ..operations._authorize import build_authorize_url
from ..types.models import ClientConfig, TokenResponse
from ..utils.pkce import PKCEGenerator
from .callback import OAuthCallbackServer

logger = logging.getLogger(__name__)


async def authenticate(
    *,
    client_id: str,
    resource_url: str,
    www_authenticate_header: str,
    client_secret: str | None = None,
    redirect_uri: str = "http://localhost:8765/callback",
    callback_port: int = 8765,
    scopes: list[str] | None = None,
    callback_timeout: int = 300,
    http_client: httpx.AsyncClient | None = None,
) -> TokenResponse:
    """Run the OAuth 2.0 authorization-code-with-PKCE flow against the issuer
    advertised by ``www_authenticate_header``.

    Targets desktop / CLI public clients running against a loopback redirect
    URI (RFC 8252). Confidential clients pass ``client_secret`` and get HTTP
    Basic auth on the token endpoint.

    Args:
        client_id: OAuth client ID.
        resource_url: The protected resource the caller is targeting. Passed
            through as the RFC 8707 ``resource`` parameter on both the
            authorize and token requests.
        www_authenticate_header: The ``WWW-Authenticate`` value from the
            resource's 401 response. Must contain a ``resource_metadata``
            URL per RFC 9728.
        client_secret: Optional client secret for confidential clients.
            Public clients (the typical PKCE use case) omit this.
        redirect_uri: Loopback redirect URI registered with the authorization
            server.
        callback_port: Port for the local callback server.
        scopes: Optional list of OAuth scopes to request.
        callback_timeout: How long to wait for the user to complete
            authorization, in seconds.
        http_client: Optional ``httpx.AsyncClient`` to use for fetching the
            protected resource metadata document. When not supplied, a
            short-lived client is created internally. The OAuth-server
            calls (server metadata discovery, token exchange) always go
            through a fresh :class:`AsyncClient`.

    Returns:
        ``TokenResponse`` from the token endpoint.

    Raises:
        ValueError: If discovery fails (no ``resource_metadata`` in the
            challenge, no ``authorization_servers`` in the metadata, or the
            authorization server is missing required endpoints).
        httpx.HTTPStatusError: If the resource metadata fetch fails.
        keycardai.oauth.OAuthHttpError: If the OAuth server's metadata or
            token endpoint returns an HTTP error.
        keycardai.oauth.OAuthProtocolError: If the token endpoint response
            contains an OAuth error.
        TimeoutError: If the user does not complete authorization within
            ``callback_timeout``.
        RuntimeError: If the authorization redirect carried an OAuth
            ``error`` parameter.
    """
    logger.info("PKCE flow starting for resource %s", resource_url)

    metadata_url = _extract_resource_metadata_url(www_authenticate_header)
    if not metadata_url:
        raise ValueError("No resource_metadata URL in WWW-Authenticate header")

    resource_metadata = await _fetch_resource_metadata(metadata_url, http_client)
    auth_servers = resource_metadata.get("authorization_servers") or []
    if not auth_servers:
        raise ValueError("No authorization_servers in resource metadata")

    auth_server_url = auth_servers[0].rstrip("/")

    auth_strategy = (
        BasicAuth(client_id, client_secret) if client_secret else NoneAuth()
    )
    config = ClientConfig(enable_metadata_discovery=True, auto_register_client=False)

    async with AsyncClient(
        base_url=auth_server_url, auth=auth_strategy, config=config
    ) as oauth_client:
        endpoints = await oauth_client.get_endpoints()
        if not endpoints.authorize or not endpoints.token:
            raise ValueError(
                "Authorization server metadata is missing authorization_endpoint "
                "or token_endpoint"
            )

        pkce = PKCEGenerator().generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        authorization_url = build_authorize_url(
            endpoints.authorize,
            client_id=client_id,
            redirect_uri=redirect_uri,
            pkce=pkce,
            resources=[resource_url],
            scope=" ".join(scopes) if scopes else None,
            state=state,
        )

        callback_server = OAuthCallbackServer(callback_port)
        await callback_server.start()
        try:
            logger.info("Opening browser for user authorization")
            webbrowser.open(authorization_url)
            code = await callback_server.wait_for_code(timeout=callback_timeout)
            logger.info("Authorization code received; exchanging for token")
        finally:
            callback_server.stop()

        return await oauth_client.exchange_authorization_code(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=pkce.code_verifier,
            client_id=client_id,
            resource=resource_url,
        )


async def _fetch_resource_metadata(
    metadata_url: str, http_client: httpx.AsyncClient | None
) -> dict[str, Any]:
    """Fetch the RFC 9728 protected resource metadata document.

    This step is paired with the protected resource (not the OAuth server),
    so it lives outside :class:`AsyncClient`.
    """
    if http_client is not None:
        response = await http_client.get(metadata_url)
        response.raise_for_status()
        return response.json()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(metadata_url)
        response.raise_for_status()
        return response.json()


def _extract_resource_metadata_url(www_authenticate: str) -> str | None:
    """Extract the ``resource_metadata`` URL from a ``WWW-Authenticate`` header.

    See RFC 9728 §5.3 for the parameter definition.
    """
    match = re.search(r'resource_metadata="([^"]+)"', www_authenticate)
    return match.group(1) if match else None
