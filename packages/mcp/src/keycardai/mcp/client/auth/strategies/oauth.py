"""Authentication strategies for MCP client connections."""

import asyncio
import dataclasses
import time
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol

from httpx import AsyncClient, Response
from httpx2 import Response as Httpx2Response

from keycardai.oauth import RefreshGrantError
from keycardai.oauth.pkce import refresh_authorization
from keycardai.oauth.types.models import AuthorizationServerMetadata

from ...logging_config import get_logger
from ...storage import NamespacedStorage
from ..oauth import (
    OAuthClientRegistrationService,
    OAuthDiscoveryService,
    OAuthFlowInitiatorService,
    OAuthTokenExchangeService,
    default_client_factory,
)
from ..oauth.exchange import stamp_tokens
from ..storage_facades import OAuthStorage

if TYPE_CHECKING:
    from ..coordinators.base import AuthCoordinator

logger = get_logger(__name__)

# A stored token this close to its expiry is refreshed before it is sent.
REFRESH_SKEW_SECONDS = 60.0

_METADATA_FIELDS = {field.name for field in dataclasses.fields(AuthorizationServerMetadata)}


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
        self._client_factory = client_factory
        # Created on first use so it binds the event loop the requests run on.
        self._refresh_lock: asyncio.Lock | None = None

    def get_strategy_namespace(self) -> str:
        """Return namespace for this strategy."""
        return "oauth"

    @staticmethod
    def _near_expiry(tokens: dict[str, Any], now: float | None = None) -> bool:
        """True when the stored token is at or within the skew of its expiry.

        Tokens saved before ``expires_at`` was recorded have an unknown expiry
        and are sent as they are; the 401 path covers them.
        """
        expires_at = tokens.get("expires_at")
        if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
            return False
        return (time.time() if now is None else now) >= float(expires_at) - REFRESH_SKEW_SECONDS

    def _lock(self) -> asyncio.Lock:
        if self._refresh_lock is None:
            self._refresh_lock = asyncio.Lock()
        return self._refresh_lock

    async def _refresh_tokens(
        self,
        seen: dict[str, Any],
        *,
        resource_url: str | None = None,
        auth_server_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Refresh the stored grant once, as the client that obtained it.

        Serialized per strategy so two requests never send the same refresh
        token. After taking the lock the stored tokens are read again: if
        another request already rotated them, those are returned without a
        second refresh. Returns the tokens now in the store, or None when the
        zone refused the refresh token (``invalid_grant``, or
        ``insufficient_authorization`` when its user no longer resolves), in which case the
        stored tokens are deleted and the caller starts a sign-in. Any other
        error propagates.
        """
        async with self._lock():
            current = await self.oauth_storage.get_tokens()
            if not current or not current.get("refresh_token"):
                return current
            if current.get("access_token") != seen.get("access_token"):
                return current

            registration = await self.oauth_storage.get_client_registration()
            if not registration or not registration.get("client_id"):
                raise ValueError("No client registration stored for the refresh")
            metadata = auth_server_metadata or await self.oauth_storage.get_auth_server_metadata()
            issuer = current.get("issuer") or (metadata or {}).get("issuer")
            resource = resource_url or current.get("resource")
            refresh_kwargs: dict[str, Any] = {}
            if metadata and metadata.get("token_endpoint"):
                refresh_kwargs["metadata"] = AuthorizationServerMetadata(
                    **{key: value for key, value in metadata.items() if key in _METADATA_FIELDS}
                )
            elif issuer:
                refresh_kwargs["issuer"] = issuer
            else:
                raise ValueError("No authorization server metadata or issuer stored for the refresh")
            if registration.get("client_secret"):
                refresh_kwargs["client_secret"] = registration["client_secret"]
            if resource:
                refresh_kwargs["resources"] = [resource]

            try:
                async with self._client_factory() as http_client:
                    response = await refresh_authorization(
                        refresh_token=current["refresh_token"],
                        client_id=registration["client_id"],
                        http_client=http_client,
                        **refresh_kwargs,
                    )
            except RefreshGrantError as error:
                # insufficient_authorization is the zone's answer when the
                # refresh token's user no longer resolves, refused the same
                # way as invalid_grant (eve.md grant lifecycle).
                if error.error in ("invalid_grant", "insufficient_authorization"):
                    logger.info(
                        f"[{self.server_name}] Refresh token refused ({error.error}); "
                        "clearing stored tokens"
                    )
                    await self.oauth_storage.delete_tokens()
                    return None
                raise

            rotated: dict[str, Any] = {
                **current,
                "access_token": response.access_token,
                "token_type": str(getattr(response.token_type, "value", response.token_type)),
            }
            rotated.pop("expires_at", None)
            rotated.pop("expires_in", None)
            if response.expires_in is not None:
                rotated["expires_in"] = response.expires_in
            if response.refresh_token:
                rotated["refresh_token"] = response.refresh_token
            if response.scope is not None:
                rotated["scope"] = " ".join(response.scope)
            rotated = stamp_tokens(rotated, resource=resource, issuer=issuer)
            await self.oauth_storage.save_tokens(rotated)
            logger.info(f"[{self.server_name}] Access token refreshed")
            return rotated

    async def get_auth_metadata(self) -> dict[str, Any]:
        """Get OAuth access token if available, refreshing one that is about to expire."""
        tokens = await self.oauth_storage.get_tokens()
        if tokens and tokens.get("refresh_token") and self._near_expiry(tokens):
            tokens = await self._refresh_tokens(tokens)
        if tokens and "access_token" in tokens:
            return {
                "headers": {
                    "Authorization": f"Bearer {tokens['access_token']}"
                }
            }
        return {}

    async def handle_challenge(
        self,
        challenge: Response | Httpx2Response,
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
        if (
            not isinstance(challenge, (Response, Httpx2Response))
            or challenge.status_code != 401
        ):
            return False

        # A stored refresh token gets one try before the user is sent back
        # through the browser. A refused one (invalid_grant) clears the
        # tokens and falls through to the flow; any other refresh error is
        # raised, and no sign-in is started for it.
        tokens = await self.oauth_storage.get_tokens()
        if tokens and tokens.get("refresh_token"):
            refreshed = await self._refresh_tokens(tokens, resource_url=resource_url)
            if refreshed and refreshed.get("access_token") != tokens.get("access_token"):
                return True

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
            # Completion cleanup always runs synchronously in the handler, so no
            # cleanup-mode kwargs are needed regardless of coordinator type
            await self.coordinator.register_completion_route(
                routing_key=flow_metadata.state,
                handler_name="oauth_completion",
                storage_namespace=self.oauth_storage._storage._namespace,
                context_id=self.context.id,
                server_name=self.server_name,
                routing_param="state",
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
