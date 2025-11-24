"""OAuth discovery service for resource and authorization server metadata."""

from collections.abc import Callable
from typing import Any

from httpx import AsyncClient, Response

from ...logging_config import get_logger
from ..storage_facades import OAuthStorage
from ._client import default_client_factory

logger = get_logger(__name__)


class OAuthDiscoveryService:
    """
    Handles OAuth 2.0 discovery protocol.

    Responsibilities:
    - Discover protected resource metadata
    - Discover authorization server metadata
    - Cache authorization server metadata
    """

    def __init__(
        self,
        storage: OAuthStorage,
        client_factory: Callable[[], AsyncClient] | None = None
    ):
        """
        Initialize OAuth discovery service.

        Args:
            storage: OAuth storage facade for caching metadata
            client_factory: Factory function that returns an AsyncClient instance.
                          If None, uses default factory that creates AsyncClient()
        """
        self.storage = storage
        self.client_factory = client_factory or default_client_factory

    async def discover_resource(self, challenge_response: Response) -> dict[str, Any]:
        """
        Discover protected resource metadata from 401 challenge.

        Fetches the OAuth protected resource metadata from the well-known endpoint
        based on the challenge response URL.

        Args:
            challenge_response: HTTP Response with 401 status code

        Returns:
            Resource metadata including authorization_servers list

        Raises:
            ValueError: If no authorization servers found in discovery response
            httpx.HTTPStatusError: If discovery request fails

        Example:
            >>> service = OAuthDiscoveryService(storage)
            >>> metadata = await service.discover_resource(challenge_response)
            >>> print(metadata["authorization_servers"])
            ["https://auth.example.com"]
        """
        #TODO: url builder pattern
        discovery_url = (
            f"{challenge_response.url.scheme}://"
            f"{challenge_response.url.netloc.decode('utf-8')}/"
            ".well-known/oauth-protected-resource/mcp"
        )

        logger.debug(f"Discovering resource metadata from: {discovery_url}")

        async with self.client_factory() as client:
            response = await client.get(discovery_url)
            response.raise_for_status()

            data = response.json()

            if "authorization_servers" not in data:
                # Don't expose full discovery response - may contain internal configuration
                raise ValueError("No authorization servers found in discovery response")

            logger.info(f"Discovered resource with {len(data['authorization_servers'])} authorization servers")
            return data

    async def discover_auth_server(
        self,
        resource_metadata: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Discover authorization server metadata.

        Uses cached metadata if available, otherwise fetches from the
        authorization server's well-known endpoint and caches it.

        Args:
            resource_metadata: Resource metadata with authorization_servers list

        Returns:
            Authorization server metadata including:
            - authorization_endpoint
            - token_endpoint
            - registration_endpoint
            - etc.

        Raises:
            ValueError: If no authorization servers found, required fields missing,
                       or all discovery attempts fail

        Example:
            >>> metadata = await service.discover_auth_server(resource_metadata)
            >>> print(metadata["authorization_endpoint"])
            "https://auth.example.com/authorize"
        """
        # TODO: cache TTL management
        cached_metadata = await self.storage.get_auth_server_metadata()
        if cached_metadata:
            logger.debug("Using cached authorization server metadata")
            return cached_metadata

        logger.debug("Fetching fresh authorization server metadata")

        auth_servers = resource_metadata.get("authorization_servers", [])
        if not auth_servers:
            raise ValueError("No authorization servers in resource metadata")

        for auth_server_url in auth_servers:
            try:
                # TODO: url builder pattern
                metadata_url = f"{auth_server_url}/.well-known/oauth-authorization-server"

                async with self.client_factory() as client:
                    response = await client.get(metadata_url)
                    response.raise_for_status()

                    data = response.json()

                    if "registration_endpoint" not in data:
                        # Don't expose full metadata - may contain internal configuration
                        raise ValueError("No registration endpoint in authorization server metadata")

                    await self.storage.save_auth_server_metadata(data)
                    logger.info(f"Discovered and cached authorization server: {auth_server_url}")

                    return data

            except Exception as e:
                logger.debug(f"Failed to discover {auth_server_url}: {e}")
                continue

        raise ValueError("Failed to discover any authorization server")

