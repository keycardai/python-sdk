"""OAuth flow initiation service."""

import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

from mcp.client.auth import PKCEParameters

from ...logging_config import get_logger
from ..storage_facades import OAuthStorage

logger = get_logger(__name__)


class FlowMetadata:
    """
    Metadata about an initiated OAuth flow.

    Contains the information needed to redirect the user and track the flow.
    """

    def __init__(
        self,
        authorization_url: str,
        state: str,
        resource_url: str
    ):
        """
        Initialize flow metadata.

        Args:
            authorization_url: URL to redirect user for authorization
            state: OAuth state parameter (used for routing callbacks)
            resource_url: Resource URL being accessed
        """
        self.authorization_url = authorization_url
        self.state = state
        self.resource_url = resource_url


class OAuthFlowInitiatorService:
    """
    Initiates OAuth 2.0 PKCE authorization flows.

    Responsibilities:
    - Generate PKCE parameters (code verifier and challenge)
    - Generate secure state parameter
    - Build authorization URL with all parameters
    - Store PKCE state for callback validation
    """

    def __init__(self, storage: OAuthStorage):
        """
        Initialize OAuth flow initiator service.

        Args:
            storage: OAuth storage facade for storing PKCE state
        """
        self.storage = storage

    async def initiate_flow(
        self,
        auth_server_metadata: dict[str, Any],
        client_info: dict[str, Any],
        resource_url: str,
        server_name: str,
        scopes: list[str] | None = None,
        pkce_ttl: timedelta | None = None
    ) -> FlowMetadata:
        """
        Initiate an OAuth 2.0 PKCE authorization flow.

        Generates PKCE parameters, builds the authorization URL, and stores
        the necessary state for callback validation.

        Args:
            auth_server_metadata: Authorization server metadata with authorization_endpoint
            client_info: Client registration info with client_id and redirect_uris
            resource_url: Resource URL being accessed (included in token request)
            server_name: Server name for this OAuth flow (needed for callback cleanup)
            scopes: Optional list of scopes to request
            pkce_ttl: Time-to-live for PKCE state (default: 10 minutes)

        Returns:
            FlowMetadata with authorization URL, state, and resource URL

        Raises:
            ValueError: If required fields are missing

        Example:
            >>> service = OAuthFlowInitiatorService(storage)
            >>> flow = await service.initiate_flow(
            ...     auth_server_metadata,
            ...     client_info,
            ...     "https://api.example.com/resource"
            ... )
            >>> print(flow.authorization_url)
            "https://auth.example.com/authorize?..."
            >>> print(flow.state)
            "abc123..."
        """
        if "authorization_endpoint" not in auth_server_metadata:
            raise ValueError("Missing authorization_endpoint in auth server metadata")
        if "client_id" not in client_info:
            raise ValueError("Missing client_id in client info")
        if not client_info.get("redirect_uris"):
            raise ValueError("Missing redirect_uris in client info")

        pkce = PKCEParameters.generate()

        state = secrets.token_urlsafe(32)

        pkce_data = {
            "code_verifier": pkce.code_verifier,
            "code_challenge": pkce.code_challenge,
            "resource_url": resource_url,
            "redirect_uri": client_info["redirect_uris"][0],
            "client_id": client_info["client_id"],
            "token_endpoint": auth_server_metadata["token_endpoint"],
            "server_name": server_name,
        }

        ttl = pkce_ttl or timedelta(minutes=10)
        await self.storage.save_pkce_state(
            state=state,
            pkce_data=pkce_data,
            ttl=ttl
        )

        logger.debug(f"Stored PKCE state with TTL: {ttl}")

        authorization_url = self._build_authorization_url(
            auth_server_metadata=auth_server_metadata,
            client_info=client_info,
            state=state,
            pkce=pkce,
            resource_url=resource_url,
            scopes=scopes
        )

        logger.debug(f"Initiated OAuth flow with state: {state[:8]}...")

        return FlowMetadata(
            authorization_url=authorization_url,
            state=state,
            resource_url=resource_url
        )

    def _build_authorization_url(
        self,
        auth_server_metadata: dict[str, Any],
        client_info: dict[str, Any],
        state: str,
        pkce: PKCEParameters,
        resource_url: str,
        scopes: list[str] | None = None
    ) -> str:
        """
        Build the OAuth authorization URL.

        Args:
            auth_server_metadata: Authorization server metadata
            client_info: Client registration info
            state: OAuth state parameter
            pkce: PKCE parameters
            resource_url: Resource URL
            scopes: Optional scopes

        Returns:
            Complete authorization URL with all parameters
        """
        auth_endpoint = auth_server_metadata["authorization_endpoint"]

        params = {
            "response_type": "code",
            "client_id": client_info["client_id"],
            "redirect_uri": client_info["redirect_uris"][0],
            "state": state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
            "resource": resource_url,
        }

        if scopes:
            params["scope"] = " ".join(scopes)

        query_string = urlencode(params)
        authorization_url = f"{auth_endpoint}?{query_string}"

        return authorization_url

