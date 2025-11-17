"""OAuth token exchange service."""

from collections.abc import Callable
from typing import Any

from httpx import AsyncClient

from ...logging_config import get_logger
from ..storage_facades import OAuthStorage
from ._client import default_client_factory

logger = get_logger(__name__)


class OAuthTokenExchangeService:
    """
    Handles OAuth 2.0 token exchange.

    Responsibilities:
    - Exchange authorization code for access/refresh tokens
    - Store tokens securely
    - Clean up PKCE state after successful exchange
    """

    def __init__(
        self,
        storage: OAuthStorage,
        client_factory: Callable[[], AsyncClient] | None = None
    ):
        """
        Initialize OAuth token exchange service.

        Args:
            storage: OAuth storage facade for token storage and PKCE state
            client_factory: Factory function that returns an AsyncClient instance.
                          If None, uses default factory that creates AsyncClient()
        """
        self.storage = storage
        self.client_factory = client_factory or default_client_factory

    async def exchange_code_for_tokens(
        self,
        code: str,
        state: str,
        auth_server_metadata: dict[str, Any],
        client_info: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Exchange authorization code for access/refresh tokens.

        Retrieves PKCE state, exchanges the code for tokens, stores the tokens,
        and cleans up the PKCE state.

        Args:
            code: Authorization code from OAuth callback
            state: OAuth state parameter (used to retrieve PKCE state)
            auth_server_metadata: Authorization server metadata with token_endpoint
            client_info: Client registration info with client_id

        Returns:
            OAuth tokens dict containing:
            - access_token: Access token for API requests
            - refresh_token: Optional refresh token
            - expires_in: Token expiration time
            - token_type: Token type (usually "Bearer")

        Raises:
            ValueError: If PKCE state not found, token endpoint missing,
                       or token exchange fails

        Example:
            >>> service = OAuthTokenExchangeService(storage)
            >>> tokens = await service.exchange_code_for_tokens(
            ...     code="auth_code_123",
            ...     state="state_abc",
            ...     auth_server_metadata=as_metadata,
            ...     client_info=client_info
            ... )
            >>> print(tokens["access_token"])
            "eyJhbGc..."
        """
        if "token_endpoint" not in auth_server_metadata:
            raise ValueError("Missing token_endpoint in auth server metadata")
        if "client_id" not in client_info:
            raise ValueError("Missing client_id in client info")

        pkce_data = await self.storage.get_pkce_state(state)
        if not pkce_data:
            raise ValueError(f"No PKCE state found for state: {state}")

        logger.debug(f"Retrieved PKCE state for exchange (state: {state[:8]}...)")

        token_endpoint = auth_server_metadata["token_endpoint"]
        tokens = await self._perform_token_exchange(
            token_endpoint=token_endpoint,
            code=code,
            client_id=client_info["client_id"],
            pkce_data=pkce_data
        )

        await self.storage.save_tokens(tokens)
        logger.info("OAuth tokens exchanged and stored successfully")

        await self.storage.delete_pkce_state(state)
        logger.debug(f"Cleaned up PKCE state (state: {state[:8]}...)")

        return tokens

    async def _perform_token_exchange(
        self,
        token_endpoint: str,
        code: str,
        client_id: str,
        pkce_data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Perform the actual token exchange request.

        Args:
            token_endpoint: Token endpoint URL
            code: Authorization code
            client_id: OAuth client ID
            pkce_data: PKCE data with code_verifier, redirect_uri, resource_url

        Returns:
            Token response dict

        Raises:
            ValueError: If token exchange fails
        """
        token_params = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pkce_data["redirect_uri"],
            "client_id": client_id,
            "code_verifier": pkce_data["code_verifier"],
        }

        if "resource_url" in pkce_data and pkce_data["resource_url"]:
            token_params["resource"] = pkce_data["resource_url"]

        logger.debug(f"Exchanging authorization code at: {token_endpoint}")

        async with self.client_factory() as client:
            response = await client.post(
                token_endpoint,
                data=token_params,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )

            if response.status_code != 200:
                # Don't log full error response - may contain sensitive details
                logger.error(f"Token exchange failed with status {response.status_code}")
                raise ValueError(f"Token exchange failed with status {response.status_code}")

            return response.json()

