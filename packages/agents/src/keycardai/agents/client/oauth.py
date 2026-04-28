"""User authentication client for calling agent services.

`AgentClient` invokes A2A agent services and handles 401 responses by running
the OAuth 2.0 authorization code with PKCE flow against the issuer advertised
by the resource. The PKCE flow itself is :func:`keycardai.oauth.pkce.authenticate`;
this module is the agent-facing wrapper that adds A2A invocation and
per-resource token caching.
"""

import logging
import warnings
from typing import Any

import httpx

from keycardai.oauth.pkce import authenticate as pkce_authenticate

from ..config import AgentServiceConfig

logger = logging.getLogger(__name__)


class AgentClient:
    """Client for calling agent services with automatic user authentication.

    Calls :func:`keycardai.oauth.pkce.authenticate` on 401 to obtain a token,
    caches the token per service URL, and retries the original ``/invoke``
    request. Also exposes ``discover_service`` for agent-card lookups.

    Example::

        from keycardai.agents import AgentServiceConfig
        from keycardai.agents.client import AgentClient

        config = AgentServiceConfig(
            service_name="My Application",
            client_id="my_client",
            client_secret="my_secret",
            identity_url="http://localhost:9000",
            zone_id="abc123",
        )

        async with AgentClient(config) as client:
            result = await client.invoke(
                service_url="http://localhost:8001",
                task="Hello world",
            )
    """

    def __init__(
        self,
        service_config: AgentServiceConfig,
        redirect_uri: str = "http://localhost:8765/callback",
        callback_port: int = 8765,
        scopes: list[str] | None = None,
    ):
        self.config = service_config
        self.redirect_uri = redirect_uri
        self.callback_port = callback_port
        self.scopes = scopes or []
        self._token_cache: dict[str, str] = {}
        self.http_client = httpx.AsyncClient(timeout=30.0)

    async def authenticate(
        self,
        service_url: str,
        www_authenticate_header: str,
    ) -> str:
        """Run PKCE flow and return the access token, caching it per service."""
        token = await pkce_authenticate(
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
            resource_url=service_url,
            www_authenticate_header=www_authenticate_header,
            redirect_uri=self.redirect_uri,
            callback_port=self.callback_port,
            scopes=self.scopes,
            http_client=self.http_client,
        )
        self._token_cache[service_url] = token.access_token
        return token.access_token

    async def invoke(
        self,
        service_url: str,
        task: str | dict[str, Any],
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call ``/invoke`` on the target service, handling 401 via PKCE flow."""
        invoke_url = f"{service_url.rstrip('/')}/invoke"
        payload = {"task": task, "inputs": inputs}

        token = self._token_cache.get(service_url)
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            response = await self.http_client.post(
                invoke_url, json=payload, headers=headers
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 401:
                raise

            www_authenticate = e.response.headers.get("WWW-Authenticate")
            if not www_authenticate:
                logger.error("No WWW-Authenticate header in 401 response")
                raise

            self._token_cache.pop(service_url, None)
            new_token = await self.authenticate(service_url, www_authenticate)

            headers["Authorization"] = f"Bearer {new_token}"
            response = await self.http_client.post(
                invoke_url, json=payload, headers=headers
            )
            response.raise_for_status()
            return response.json()

    async def discover_service(self, service_url: str) -> dict[str, Any]:
        """Fetch the ``.well-known/agent-card.json`` document for a service."""
        card_url = f"{service_url.rstrip('/')}/.well-known/agent-card.json"
        response = await self.http_client.get(card_url)
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self.http_client.aclose()

    async def __aenter__(self) -> "AgentClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


# Backward compatibility alias
A2AServiceClientWithOAuth = AgentClient


def __getattr__(name: str):
    """Deprecation re-export of OAuthCallbackServer.

    The class moved to ``keycardai.oauth.pkce``; this wrapper preserves the old
    import path with a runtime warning so any direct importer notices.
    """
    if name == "OAuthCallbackServer":
        warnings.warn(
            "keycardai.agents.client.oauth.OAuthCallbackServer is deprecated; "
            "import from keycardai.oauth.pkce instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        from keycardai.oauth.pkce import OAuthCallbackServer

        return OAuthCallbackServer
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )
