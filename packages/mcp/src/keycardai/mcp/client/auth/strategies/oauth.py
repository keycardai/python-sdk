"""Authentication strategies for MCP client connections."""

from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol

from httpx import AsyncClient, Response

from ...logging_config import get_logger
from ...storage import NamespacedStorage
from ..oauth import (
    OAuthClientRegistrationService,
    OAuthDiscoveryService,
    OAuthFlowInitiatorService,
    OAuthTokenExchangeService,
    default_client_factory,
)
from ..storage_facades import OAuthStorage

if TYPE_CHECKING:
    from ..coordinators.base import AuthCoordinator

logger = get_logger(__name__)


class AuthStrategy(Protocol):
    """
    Protocol for authentication strategies.

    Each strategy manages its own storage namespace and implements
    the complete authentication lifecycle.
    """

    def get_strategy_namespace(self) -> str:
        """
        Return the storage namespace for this strategy.

        Returns:
            Namespace identifier (e.g., 'oauth', 'api_key', 'none')
        """
        ...

    async def get_auth_metadata(self) -> dict[str, Any]:
        """
        Get authentication metadata to add to requests.

        Returns:
            Dict with headers and other auth metadata
        """
        ...

    async def handle_challenge(
        self,
        challenge: Any,
        resource_url: str
    ) -> bool:
        """
        Handle authentication challenge.

        Args:
            challenge: Challenge response (e.g., 401 HTTP response)
            resource_url: URL of the protected resource

        Returns:
            True if challenge was handled, False otherwise
        """
        ...


class NoAuthStrategy:
    """No authentication required."""

    def __init__(self, storage: NamespacedStorage, context: Any, coordinator: "AuthCoordinator"):
        """
        Initialize no-auth strategy.

        Args:
            storage: Strategy's storage namespace (not used for no-auth)
            context: Context (not used for no-auth)
            coordinator: Auth coordinator (not used for no-auth)
        """
        self.storage = storage
        self.context = context
        self.coordinator = coordinator

    def get_strategy_namespace(self) -> str:
        """Return namespace for this strategy."""
        return "none"

    async def get_auth_metadata(self) -> dict[str, Any]:
        """No auth metadata."""
        return {}

    async def handle_challenge(
        self,
        challenge: Any,
        resource_url: str
    ) -> bool:
        """No challenge handling."""
        return False


class ApiKeyStrategy:
    """API key authentication strategy."""

    def __init__(
        self,
        api_key: str,
        storage: NamespacedStorage,
        context: Any,
        coordinator: "AuthCoordinator",
        header_name: str = "X-API-Key"
    ):
        """
        Initialize API key strategy.

        Args:
            api_key: API key for authentication
            storage: Strategy's storage namespace (not used for API key)
            context: Context (not used for API key)
            coordinator: Auth coordinator (not used for API key)
            header_name: HTTP header name for API key
        """
        self.api_key = api_key
        self.header_name = header_name
        self.storage = storage
        self.context = context
        self.coordinator = coordinator

    def get_strategy_namespace(self) -> str:
        """Return namespace for this strategy."""
        return "api_key"

    async def get_auth_metadata(self) -> dict[str, Any]:
        """Return API key header."""
        return {
            "headers": {
                self.header_name: self.api_key
            }
        }

    async def handle_challenge(
        self,
        challenge: Any,
        resource_url: str
    ) -> bool:
        """No challenge handling for API keys."""
        return False


class OAuthStrategy:
    """
    OAuth 2.0 PKCE authentication strategy.

    Orchestrates OAuth flow using specialized services:
    - Discovery: Find resource and authorization server metadata
    - Registration: Register OAuth client dynamically
    - Flow: Initiate authorization flow with PKCE
    - Exchange: Exchange authorization code for tokens

    Each server connection gets its own instance with isolated storage.
    """

    def __init__(
        self,
        server_name: str,
        storage: NamespacedStorage,
        context: Any,
        coordinator: "AuthCoordinator",
        client_name: str | None = None,
        client_factory: Callable[[], AsyncClient] | None = None
    ):
        """
        Initialize OAuth strategy.

        Args:
            server_name: Name of the server (for logging and routing)
            storage: Strategy's storage namespace
            context: Context for identity
            coordinator: Auth coordinator for callbacks and auth state
            client_name: OAuth client name (default: "MCP Client - {server_name}")
            client_factory: Factory function that returns an AsyncClient instance.
                          If None, uses default factory that creates AsyncClient()
        """
        self.server_name = server_name
        self.context = context
        self.coordinator = coordinator

        self.oauth_storage = OAuthStorage(storage)

        client_factory = client_factory or default_client_factory
        client_name = client_name or f"MCP Client - {server_name}"

        self.discovery = OAuthDiscoveryService(
            storage=self.oauth_storage,
            client_factory=client_factory
        )
        self.registration = OAuthClientRegistrationService(
            storage=self.oauth_storage,
            client_name=client_name,
            client_factory=client_factory
        )
        self.flow_initiator = OAuthFlowInitiatorService(
            storage=self.oauth_storage
        )
        self.token_exchanger = OAuthTokenExchangeService(
            storage=self.oauth_storage,
            client_factory=client_factory
        )

    def get_strategy_namespace(self) -> str:
        """Return namespace for this strategy."""
        return "oauth"

    async def get_auth_metadata(self) -> dict[str, Any]:
        """Get OAuth access token if available."""
        tokens = await self.oauth_storage.get_tokens()
        if tokens and "access_token" in tokens:
            return {
                "headers": {
                    "Authorization": f"Bearer {tokens['access_token']}"
                }
            }
        return {}

    async def handle_challenge(
        self,
        challenge: Response,
        resource_url: str
    ) -> bool:
        """
        Handle 401 challenge by orchestrating OAuth flow.

        Coordinates all OAuth services to complete the authentication flow:
        1. Discovery: Find resource and authorization server metadata
        2. Registration: Register OAuth client (or reuse existing)
        3. Flow: Initiate PKCE authorization flow
        4. Routing: Register callback handler for token exchange

        Args:
            challenge: HTTP 401 response containing OAuth challenge
            resource_url: URL of the protected resource

        Returns:
            True if challenge was handled successfully, False otherwise
        """
        if not isinstance(challenge, Response) or challenge.status_code != 401:
            return False

        try:
            # Step 1: Discover resource and authorization server metadata
            logger.debug(f"[{self.server_name}] Discovering OAuth endpoints")
            resource_metadata = await self.discovery.discover_resource(challenge)
            auth_server_metadata = await self.discovery.discover_auth_server(resource_metadata)

            # Step 2: Register OAuth client (or reuse existing registration)
            redirect_uris = await self.coordinator.get_redirect_uris()
            if not redirect_uris:
                raise ValueError("Coordinator doesn't provide redirect URIs")

            client_info = await self.registration.get_or_register_client(
                auth_server_metadata=auth_server_metadata,
                redirect_uris=redirect_uris
            )

            # Step 3: Initiate OAuth authorization flow
            flow_metadata = await self.flow_initiator.initiate_flow(
                auth_server_metadata=auth_server_metadata,
                client_info=client_info,
                resource_url=resource_url,
                server_name=self.server_name
            )

            # Step 4: Register completion route for token exchange
            # Coordinators that require synchronous cleanup (e.g., LocalAuthCoordinator
            # with blocking wait patterns) will have it, others use background cleanup
            handler_kwargs = {}
            if self.coordinator.requires_synchronous_cleanup:
                handler_kwargs["run_cleanup_in_background"] = False
                logger.debug(f"[{self.server_name}] Using synchronous cleanup for {type(self.coordinator).__name__}")
            else:
                logger.debug(f"[{self.server_name}] Using background cleanup for {type(self.coordinator).__name__}")

            await self.coordinator.register_completion_route(
                routing_key=flow_metadata.state,
                handler_name="oauth_completion",
                storage_namespace=self.oauth_storage._storage._namespace,
                context_id=self.context.id,
                server_name=self.server_name,
                routing_param="state",
                handler_kwargs=handler_kwargs,
                metadata=self.context.metadata,
                ttl=timedelta(minutes=10)
            )

            # Step 5: Mark authentication as pending
            await self.coordinator.set_auth_pending(
                context_id=self.context.id,
                server_name=self.server_name,
                auth_metadata={
                    "authorization_url": flow_metadata.authorization_url,
                    "state": flow_metadata.state
                },
                ttl=timedelta(minutes=5)
            )

            # Step 6: Initiate user redirect
            await self.coordinator.handle_redirect(
                flow_metadata.authorization_url,
                metadata={"server_name": self.server_name}
            )

            logger.info(f"[{self.server_name}] OAuth flow initiated successfully")
            return True

        except Exception as e:
            logger.error(f"[{self.server_name}] OAuth challenge handling failed: {e}", exc_info=True)
            return False



def create_auth_strategy(
    auth_config: dict[str, Any] | None,
    server_name: str,
    connection_storage: NamespacedStorage,
    context: Any,
    coordinator: "AuthCoordinator"
) -> AuthStrategy:
    """
    Factory function to create auth strategy from configuration.

    Args:
        auth_config: Auth configuration dict with 'type' and strategy-specific params
        server_name: Name of the server (for OAuth client naming)
        connection_storage: Connection's storage namespace (will create strategy sub-namespace)
        context: Context for identity
        coordinator: Auth coordinator for callbacks

    Returns:
        AuthStrategy instance

    Examples:
        >>> create_auth_strategy(None, "server1", storage, ctx, coord)
        NoAuthStrategy(...)

        >>> create_auth_strategy({"type": "oauth"}, "slack", storage, ctx, coord)
        OAuthStrategy(server_name="slack", ...)

        >>> create_auth_strategy({"type": "api_key", "key": "secret"}, "api", storage, ctx, coord)
        ApiKeyStrategy(api_key="secret", ...)
    """
    auth_type = auth_config.get("type") if auth_config else None

    # Map auth type to namespace
    if auth_type == "oauth":
        strategy_namespace = "oauth"
    elif auth_type == "api_key":
        strategy_namespace = "api_key"
    else:
        strategy_namespace = "none"

    strategy_storage = connection_storage.get_namespace(strategy_namespace)

    if not auth_config or auth_type == "none" or auth_type is None:
        return NoAuthStrategy(strategy_storage, context, coordinator)

    if auth_type == "oauth":
        return OAuthStrategy(
            server_name=server_name,
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
            client_name=auth_config.get("client_name")
        )
    elif auth_type == "api_key":
        return ApiKeyStrategy(
            api_key=auth_config["key"],
            storage=strategy_storage,
            context=context,
            coordinator=coordinator,
            header_name=auth_config.get("header_name", "X-API-Key")
        )
    else:
        raise ValueError(f"Unknown auth type: {auth_type}")

