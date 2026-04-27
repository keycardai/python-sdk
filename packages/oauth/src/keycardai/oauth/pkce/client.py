"""High-level PKCE flow client for browser-based OAuth 2.0 user authentication.

Drives the full authorization code with PKCE flow:

1. Parse the ``WWW-Authenticate`` challenge from the protected resource (RFC 9728)
2. Fetch protected resource metadata, then authorization server metadata (RFC 8414)
3. Generate a PKCE verifier/challenge pair (RFC 7636) and a CSRF state value
4. Start a local callback server, open the user's browser at the authorize endpoint
5. Receive the authorization code from the redirect
6. Exchange the code at the token endpoint and return a :class:`TokenResponse`

The actual PKCE primitives (``PKCEGenerator``, ``build_authorize_url``,
``exchange_authorization_code_async``) live elsewhere in
:mod:`keycardai.oauth`; this module is the thin orchestration layer that
wires them together with browser + callback machinery.
"""

import logging
import re
import secrets
import webbrowser
from typing import Any
from urllib.parse import urlencode

import httpx

from ..utils.pkce import PKCEGenerator
from .callback import OAuthCallbackServer

logger = logging.getLogger(__name__)


class PKCEClient:
    """OAuth 2.0 user-login client using authorization code with PKCE.

    Targets desktop / CLI public clients running against a loopback redirect URI
    (RFC 8252). Intended to be used as an async context manager.

    Example::

        async with PKCEClient(client_id="my-app") as pkce:
            token = await pkce.authenticate(
                resource_url="https://api.example.com",
                www_authenticate_header=resp.headers["WWW-Authenticate"],
            )

    Args:
        client_id: OAuth client ID.
        client_secret: Optional client secret. Public clients (the PKCE use
            case) usually omit this.
        redirect_uri: Loopback redirect URI registered with the authorization
            server.
        callback_port: Port for the local callback server.
        scopes: Optional list of OAuth scopes to request.
        timeout: HTTP timeout for metadata and token requests when this
            client owns its ``httpx.AsyncClient``. Ignored if ``http_client``
            is supplied.
        http_client: Optional ``httpx.AsyncClient`` to share with another
            component. When provided, ``close()`` does not close it; the
            owner of the injected client is responsible for its lifecycle.
    """

    def __init__(
        self,
        client_id: str,
        *,
        client_secret: str | None = None,
        redirect_uri: str = "http://localhost:8765/callback",
        callback_port: int = 8765,
        scopes: list[str] | None = None,
        timeout: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.callback_port = callback_port
        self.scopes = scopes or []
        if http_client is not None:
            self._http = http_client
            self._owns_http = False
        else:
            self._http = httpx.AsyncClient(timeout=timeout)
            self._owns_http = True

    async def authenticate(
        self,
        resource_url: str,
        www_authenticate_header: str,
        callback_timeout: int = 300,
    ) -> dict[str, Any]:
        """Run the PKCE flow against the issuer advertised by the resource.

        Args:
            resource_url: The protected resource the caller is targeting.
                Passed through as the RFC 8707 ``resource`` parameter.
            www_authenticate_header: The ``WWW-Authenticate`` value from the
                resource's 401 response. Must contain a ``resource_metadata``
                URL per RFC 9728.
            callback_timeout: How long to wait for the user to complete
                authorization, in seconds.

        Returns:
            The raw token endpoint response as a dict (always includes
            ``access_token``; usually includes ``token_type``, ``expires_in``,
            and may include ``refresh_token``, ``id_token``, ``scope``).

        Raises:
            ValueError: If discovery fails (no ``resource_metadata`` in the
                challenge, no ``authorization_servers`` in the metadata, or
                missing endpoints in the auth server metadata).
            httpx.HTTPStatusError: If a metadata fetch or token exchange
                request fails.
            TimeoutError: If the user does not complete authorization within
                ``callback_timeout``.
            RuntimeError: If the authorization redirect carried an OAuth
                ``error`` parameter.
        """
        logger.info("PKCE flow starting for resource %s", resource_url)

        metadata_url = _extract_resource_metadata_url(www_authenticate_header)
        if not metadata_url:
            raise ValueError(
                "No resource_metadata URL in WWW-Authenticate header"
            )

        resource_metadata = await self._fetch_json(metadata_url)
        auth_servers = resource_metadata.get("authorization_servers") or []
        if not auth_servers:
            raise ValueError("No authorization_servers in resource metadata")

        auth_server_url = auth_servers[0].rstrip("/")
        auth_server_metadata = await self._fetch_json(
            f"{auth_server_url}/.well-known/oauth-authorization-server"
        )

        authorization_endpoint = auth_server_metadata.get("authorization_endpoint")
        token_endpoint = auth_server_metadata.get("token_endpoint")
        if not authorization_endpoint or not token_endpoint:
            raise ValueError(
                "Missing authorization_endpoint or token_endpoint in metadata"
            )

        pkce = PKCEGenerator().generate_pkce_pair()
        state = secrets.token_urlsafe(32)

        callback_server = OAuthCallbackServer(self.callback_port)
        await callback_server.start()
        try:
            authorization_url = self._build_authorization_url(
                authorization_endpoint, resource_url, pkce.code_challenge, state
            )
            logger.info("Opening browser for user authorization")
            webbrowser.open(authorization_url)

            code = await callback_server.wait_for_code(timeout=callback_timeout)
            logger.info("Authorization code received; exchanging for token")

            return await self._exchange_code(
                token_endpoint, code, pkce.code_verifier, resource_url
            )
        finally:
            callback_server.stop()

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "PKCEClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def _fetch_json(self, url: str) -> dict[str, Any]:
        response = await self._http.get(url)
        response.raise_for_status()
        return response.json()

    def _build_authorization_url(
        self,
        authorization_endpoint: str,
        resource_url: str,
        code_challenge: str,
        state: str,
    ) -> str:
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "resource": resource_url,
        }
        if self.scopes:
            params["scope"] = " ".join(self.scopes)
        return f"{authorization_endpoint}?{urlencode(params)}"

    async def _exchange_code(
        self,
        token_endpoint: str,
        code: str,
        code_verifier: str,
        resource_url: str,
    ) -> dict[str, Any]:
        token_params: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": code_verifier,
            "resource": resource_url,
        }
        auth_tuple = None
        if self.client_secret:
            auth_tuple = (self.client_id, self.client_secret)

        response = await self._http.post(
            token_endpoint,
            data=token_params,
            auth=auth_tuple,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        return response.json()


def _extract_resource_metadata_url(www_authenticate: str) -> str | None:
    """Extract the ``resource_metadata`` URL from a ``WWW-Authenticate`` header.

    See RFC 9728 §5.3 for the parameter definition.
    """
    match = re.search(r'resource_metadata="([^"]+)"', www_authenticate)
    return match.group(1) if match else None
