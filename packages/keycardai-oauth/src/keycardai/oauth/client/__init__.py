"""OAuth 2.0 unified client implementation.

This module provides the main Client class that handles all OAuth 2.0 operations
including token exchange, introspection, and revocation.
"""

from ._auth import (
    ClientAuth,
    ClientSecretBasic,
    ClientSecretPost,
    MutualTLS,
    PrivateKeyJWT,
)
from ._discovery import DiscoveryClient
from ._http import HTTPClient
from ._operations import (
    IntrospectionClient,
    PushedAuthorizationClient,
    RevocationClient,
    TokenExchangeClient,
)


class Client:
    """Unified OAuth 2.0 client for all operations.

    This client provides a single interface for all OAuth 2.0 operations
    including token exchange, introspection, revocation, and discovery.

    Example:
        # Initialize with server discovery
        client = await Client.from_issuer(
            "https://auth.example.com",
            client_id="my_client",
            client_secret="my_secret"
        )

        # Or initialize with explicit endpoints
        client = Client(
            token_endpoint="https://auth.example.com/token",
            introspection_endpoint="https://auth.example.com/introspect",
            revocation_endpoint="https://auth.example.com/revoke",
            client_id="my_client",
            client_secret="my_secret"
        )

        # Use the client
        token_response = await client.exchange_token(
            subject_token="old_token",
            subject_token_type="urn:ietf:params:oauth:token-type:access_token"
        )
    """

    def __init__(
        self,
        token_endpoint: str | None = None,
        introspection_endpoint: str | None = None,
        revocation_endpoint: str | None = None,
        par_endpoint: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        auth: ClientAuth | None = None,
        http_client: HTTPClient | None = None,
    ):
        """Initialize OAuth 2.0 client.

        Args:
            token_endpoint: Token endpoint URL
            introspection_endpoint: Token introspection endpoint URL
            revocation_endpoint: Token revocation endpoint URL
            par_endpoint: Pushed authorization request endpoint URL
            client_id: Client identifier
            client_secret: Client secret
            auth: Custom authentication strategy
            http_client: Custom HTTP client
        """
        self.token_endpoint = token_endpoint
        self.introspection_endpoint = introspection_endpoint
        self.revocation_endpoint = revocation_endpoint
        self.par_endpoint = par_endpoint

        # Set up authentication
        if auth is None and client_id and client_secret:
            self.auth = ClientSecretBasic(client_id, client_secret)
        else:
            self.auth = auth

        self.http_client = http_client or HTTPClient()

        # Initialize operation clients as needed
        self._token_exchange_client = None
        self._introspection_client = None
        self._revocation_client = None
        self._par_client = None

    @classmethod
    async def from_issuer(
        cls,
        issuer_url: str,
        client_id: str,
        client_secret: str,
        auth: ClientAuth | None = None,
    ) -> "Client":
        """Create client by discovering server metadata.

        Args:
            issuer_url: OAuth 2.0 issuer URL
            client_id: Client identifier
            client_secret: Client secret
            auth: Custom authentication strategy

        Returns:
            Configured OAuth 2.0 client
        """
        discovery_client = DiscoveryClient()
        metadata = await discovery_client.discover_metadata(issuer_url)

        return cls(
            token_endpoint=metadata.token_endpoint,
            introspection_endpoint=metadata.introspection_endpoint,
            revocation_endpoint=metadata.revocation_endpoint,
            client_id=client_id,
            client_secret=client_secret,
            auth=auth,
        )

    async def exchange_token(self, **kwargs):
        """Exchange an OAuth 2.0 token for a new token."""
        if not self.token_endpoint:
            raise ValueError("Token endpoint not configured")

        if self._token_exchange_client is None:
            self._token_exchange_client = TokenExchangeClient(self.token_endpoint)

        return await self._token_exchange_client.exchange_token(**kwargs)

    async def introspect_token(self, **kwargs):
        """Introspect an OAuth 2.0 token."""
        if not self.introspection_endpoint:
            raise ValueError("Introspection endpoint not configured")

        if self._introspection_client is None:
            self._introspection_client = IntrospectionClient(
                self.introspection_endpoint,
                self.auth.client_id if hasattr(self.auth, 'client_id') else None,
                self.auth.client_secret if hasattr(self.auth, 'client_secret') else None,
            )

        return await self._introspection_client.introspect_token(**kwargs)

    async def revoke_token(self, **kwargs):
        """Revoke an OAuth 2.0 token."""
        if not self.revocation_endpoint:
            raise ValueError("Revocation endpoint not configured")

        if self._revocation_client is None:
            self._revocation_client = RevocationClient(
                self.revocation_endpoint,
                self.auth.client_id if hasattr(self.auth, 'client_id') else None,
                self.auth.client_secret if hasattr(self.auth, 'client_secret') else None,
            )

        return await self._revocation_client.revoke_token(**kwargs)


__all__ = [
    "Client",
    "TokenExchangeClient",
    "IntrospectionClient",
    "RevocationClient",
    "PushedAuthorizationClient",
    "DiscoveryClient",
    "HTTPClient",
    "ClientAuth",
    "ClientSecretBasic",
    "ClientSecretPost",
    "PrivateKeyJWT",
    "MutualTLS",
]
