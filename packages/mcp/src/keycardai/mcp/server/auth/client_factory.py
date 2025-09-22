"""Client factory for OAuth client creation.

This module provides the ClientFactory protocol and DefaultClientFactory implementation
to enable dependency injection and customization of OAuth client creation.
"""

from typing import Protocol

from keycardai.oauth import AsyncClient, Client, ClientConfig
from keycardai.oauth.http.auth import AuthStrategy


class ClientFactory(Protocol):
    """Protocol for creating OAuth clients."""
    def create_client(self, base_url: str, auth: AuthStrategy | None = None, config: ClientConfig | None = None) -> Client:
        """Create an OAuth client."""
        pass

    def create_async_client(self, base_url: str, auth: AuthStrategy | None = None, config: ClientConfig | None = None) -> AsyncClient:
        """Create an asynchronous OAuth client."""
        pass


class DefaultClientFactory(ClientFactory):
    """Default client factory."""

    def create_client(self, base_url: str, auth: AuthStrategy | None = None, config: ClientConfig | None = None) -> Client:
        """Create discovery client."""
        client_config = config or ClientConfig(enable_metadata_discovery=True, auto_register_client=False)
        return Client(base_url, auth=auth, config=client_config)

    def create_async_client(self, base_url: str, auth: AuthStrategy | None = None, config: ClientConfig | None = None) -> AsyncClient:
        """Create an asynchronous OAuth client."""
        client_config = config or ClientConfig(enable_metadata_discovery=True, auto_register_client=False)
        return AsyncClient(base_url, auth=auth, config=client_config)
