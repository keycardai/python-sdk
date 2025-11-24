"""OAuth dynamic client registration service."""

from collections.abc import Callable
from typing import Any

from httpx import AsyncClient

from keycardai.oauth.types.models import OAuthClientMetadata

from ...logging_config import get_logger
from ..storage_facades import OAuthStorage
from ._client import default_client_factory

logger = get_logger(__name__)


class OAuthClientRegistrationService:
    """
    Handles OAuth 2.0 dynamic client registration.

    Responsibilities:
    - Register new OAuth clients with authorization servers
    - Cache client registration information
    - Reuse existing client registrations when possible
    """

    def __init__(
        self,
        storage: OAuthStorage,
        client_name: str,
        client_factory: Callable[[], AsyncClient] | None = None
    ):
        """
        Initialize OAuth client registration service.

        Args:
            storage: OAuth storage facade for caching registration info
            client_name: OAuth client name for registration
            client_factory: Factory function that returns an AsyncClient instance.
                          If None, uses default factory that creates AsyncClient()
        """
        self.storage = storage
        self.client_name = client_name
        self.client_factory = client_factory or default_client_factory

    async def get_or_register_client(
        self,
        auth_server_metadata: dict[str, Any],
        redirect_uris: list[str]
    ) -> dict[str, Any]:
        """
        Get existing client registration or register a new one.

        Checks storage for an existing registration first. If found and redirect URIs
        match, returns the cached registration. Otherwise, registers a new client.

        Args:
            auth_server_metadata: Authorization server metadata with registration_endpoint
            redirect_uris: Redirect URIs to register for the client

        Returns:
            Client registration information including:
            - client_id: The OAuth client identifier
            - redirect_uris: Registered redirect URIs
            - grant_types: Supported grant types
            - etc.

        Raises:
            ValueError: If registration endpoint not found
            httpx.HTTPStatusError: If registration request fails

        Example:
            >>> service = OAuthClientRegistrationService(storage, "My App")
            >>> client_info = await service.get_or_register_client(
            ...     auth_server_metadata,
            ...     ["http://localhost:8080/callback"]
            ... )
            >>> print(client_info["client_id"])
            "abc123..."
        """
        existing_client = await self.storage.get_client_registration()
        if existing_client:
            logger.debug(f"Found existing client registration: {existing_client.get('client_id')}")

            existing_uris = set(existing_client.get("redirect_uris", []))
            requested_uris = set(redirect_uris)

            if existing_uris == requested_uris:
                logger.info("Reusing existing client registration (redirect URIs match)")
                return existing_client
            else:
                logger.info(
                    "Redirect URIs changed, re-registering client "
                    f"(existing: {existing_uris}, requested: {requested_uris})"
                )

        return await self._register_new_client(auth_server_metadata, redirect_uris)

    async def _register_new_client(
        self,
        auth_server_metadata: dict[str, Any],
        redirect_uris: list[str]
    ) -> dict[str, Any]:
        """
        Register a new OAuth client with the authorization server.

        Args:
            auth_server_metadata: Authorization server metadata
            redirect_uris: Redirect URIs for the client

        Returns:
            Client registration information

        Raises:
            ValueError: If registration endpoint is missing
            httpx.HTTPStatusError: If registration request fails
        """
        registration_endpoint = auth_server_metadata.get("registration_endpoint")
        if not registration_endpoint:
            raise ValueError("Authorization server does not support dynamic registration")

        logger.info(f"Registering new OAuth client: {self.client_name}")

        client_metadata = OAuthClientMetadata(
            client_name=self.client_name,
            redirect_uris=redirect_uris,
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none"
        )

        async with self.client_factory() as client:
            response = await client.post(
                registration_endpoint,
                json=client_metadata.model_dump(mode="json"),
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()

            data = response.json()

            for field in ['client_uri', 'logo_uri', 'policy_uri', 'tos_uri']:
                if field in data and data[field] == '':
                    data[field] = None

        await self.storage.save_client_registration(data)
        logger.info(f"Registered and cached client: {data.get('client_id')}")

        return data

