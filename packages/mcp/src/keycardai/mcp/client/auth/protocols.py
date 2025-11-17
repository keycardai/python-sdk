"""Protocol definitions for auth subsystem components."""

from datetime import timedelta
from typing import Any, Protocol


class RedirectHandler(Protocol):
    """
    Protocol for handling OAuth redirects during auth flows.
    """

    async def get_redirect_uris(self) -> list[str]:
        """
        Get available redirect URIs for OAuth registration.

        Returns:
            List of redirect URIs to register with OAuth server
        """
        ...

    async def initiate_redirect(self, url: str, metadata: dict[str, Any]) -> None:
        """
        Initiate user redirect to authorization URL.

        Args:
            url: Authorization URL to redirect user to
            metadata: Additional metadata (e.g., server_name, state)
        """
        ...


class AuthStateManager(Protocol):
    """
    Protocol for managing pending auth state.

    Tracks authentication flows that are pending user action
    (e.g., OAuth flows waiting for user to authorize in browser).
    """

    async def mark_auth_pending(
        self,
        context_id: str,
        server_name: str,
        metadata: dict[str, Any],
        ttl: timedelta | None = None
    ) -> None:
        """
        Mark authentication as pending for a context/server.

        Called by strategies when they initiate an auth flow that requires
        user interaction (e.g., OAuth redirect).

        Args:
            context_id: Context identifier (e.g., "user:alice")
            server_name: Server name requiring auth (e.g., "slack")
            metadata: Strategy-specific metadata (e.g., authorization_url, state)
            ttl: Optional expiration time for pending state
        """
        ...

    async def get_auth_status(
        self,
        context_id: str,
        server_name: str
    ) -> dict[str, Any] | None:
        """
        Get pending auth metadata, if any.

        Args:
            context_id: Context identifier
            server_name: Server name to check

        Returns:
            Auth metadata if pending, None otherwise
        """
        ...

    async def clear_auth_status(
        self,
        context_id: str,
        server_name: str
    ) -> None:
        """
        Clear pending auth state.

        Called when auth flow completes (successfully or not).

        Args:
            context_id: Context identifier
            server_name: Server name
        """
        ...


class CompletionRegistrar(Protocol):
    """
    Protocol for registering auth completion handlers.

    Used in stateless environments (e.g., serverless) where callback
    handling happens in a different process/invocation than the one
    that initiated the auth flow.
    """

    async def register_completion_handler(
        self,
        routing_key: str,
        handler_name: str,
        storage_path: str,
        context_id: str,
        server_name: str,
        ttl: timedelta | None = None
    ) -> None:
        """
        Register a completion handler for stateless routing.

        Stores metadata needed to route incoming callbacks to the correct
        handler with the correct storage context.

        Args:
            routing_key: Key to route callback (e.g., OAuth state parameter)
            handler_name: Name of registered handler (e.g., "oauth_callback")
            storage_path: Full storage namespace path for handler's data
            context_id: Context identifier
            server_name: Server name (for cleanup)
            ttl: Optional expiration time for routing metadata

        Example:
            await registrar.register_completion_handler(
                routing_key="abc123",
                handler_name="oauth_callback",
                storage_path="client:alice:server:slack:connection:oauth",
                context_id="alice",
                server_name="slack",
                ttl=timedelta(minutes=10)
            )
        """
        ...


class CompletionHandler(Protocol):
    """
    Protocol for auth completion handler functions.

    Completion handlers process callbacks from auth providers
    (e.g., OAuth authorization code callbacks).
    """

    async def __call__(
        self,
        coordinator: Any,  # AuthCoordinator
        storage: Any,  # NamespacedStorage
        params: dict[str, str]
    ) -> dict[str, Any]:
        """
        Handle auth completion with given parameters.

        Args:
            coordinator: AuthCoordinator instance (for cleanup)
            storage: Strategy's namespaced storage
            params: Callback parameters (e.g., {"code": "...", "state": "..."})

        Returns:
            Dict with completion result and metadata

        Raises:
            ValueError: If required parameters are missing or invalid

        Example:
            # OAuth completion handler
            async def oauth_completion(coordinator, storage, params):
                code = params["code"]
                state = params["state"]
                # Exchange code for tokens...
                return {"success": True}
        """
        ...

