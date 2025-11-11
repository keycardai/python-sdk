"""Authentication coordination for MCP clients."""

from abc import ABC, abstractmethod
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from ...context import Context
from ...logging_config import get_logger
from ...storage import InMemoryBackend, NamespacedStorage, StorageBackend
from ..events import CompletionEvent, CompletionSubscriber
from ..handlers import (
    CompletionHandlerRegistry,
    CompletionRouter,
    get_default_handler_registry,
)
from ..storage_facades import AuthStateStorage

if TYPE_CHECKING:
    from .endpoint_managers import EndpointManager

logger = get_logger(__name__)


class AuthCoordinator(ABC):
    """
    Abstract base for authentication coordination.

    Facade coordinating authentication components:
    - EndpointManager: HTTP redirect endpoints (local server or remote)
    - AuthStateStorage: Pending auth state management
    - CompletionRouter: Completion routing and handler invocation
    - Subscribers: Observer notifications for completion events
    """

    def __init__(
        self,
        backend: StorageBackend | None = None,
        endpoint_manager: "EndpointManager | None" = None,
        handler_registry: CompletionHandlerRegistry | None = None
    ):
        """
        Initialize coordinator.

        Args:
            backend: Storage backend (creates InMemoryBackend if None)
            endpoint_manager: Endpoint manager for redirects (required for concrete subclasses)
            handler_registry: Completion handler registry (uses default global registry if None)
        """
        if backend is None:
            backend = InMemoryBackend()

        self._backend = backend

        coordinator_storage = NamespacedStorage(backend, "auth_coordinator")

        registry = handler_registry or get_default_handler_registry()
        registry_dict = registry._handlers

        self.endpoint_manager = endpoint_manager
        self.state_store = AuthStateStorage(coordinator_storage)
        self.completion_router = CompletionRouter(
            self.state_store,
            registry_dict
        )

        self._contexts: dict[str, Context] = {}

        self._subscribers: list[CompletionSubscriber] = []

    @property
    def storage(self) -> NamespacedStorage:
        """
        Get the coordinator's storage (for backward compatibility).

        Returns the underlying storage used by state_store.
        """
        return self.state_store._storage

    @property
    @abstractmethod
    def endpoint_type(self) -> str:
        """
        Type of endpoint this coordinator uses.

        Returns:
            "local" for LocalAuthCoordinator, "remote" for StarletteAuthCoordinator
        """
        pass

    @property
    def requires_synchronous_cleanup(self) -> bool:
        """
        Whether this coordinator requires synchronous callback cleanup.

        Override in subclasses if they need cleanup to complete before
        callback response (e.g., LocalAuthCoordinator needs this to avoid
        race conditions with blocking wait patterns).

        Returns:
            False by default (asynchronous cleanup is fine)
        """
        return False

    def create_context(self, context_id: str, metadata: dict[str, Any] | None = None) -> Context:
        """
        Create or retrieve a context with properly scoped storage.

        Idempotent - returns same Context instance for same context_id.

        Args:
            context_id: Context identifier (e.g., "user:alice" or just "alice")
            metadata: Optional metadata dict to attach to context (e.g., user info, session data)

        Returns:
            Context instance with namespaced storage
        """
        if context_id not in self._contexts:
            # Create isolated client namespace using the backend directly
            # This ensures proper isolation at the client level
            context_storage = NamespacedStorage(self._backend, f"client:{context_id}")
            self._contexts[context_id] = Context(
                id=context_id,
                storage=context_storage,
                coordinator=self,
                metadata=metadata
            )
        return self._contexts[context_id]

    # Subscriber management

    def subscribe(self, subscriber: CompletionSubscriber) -> None:
        """Subscribe to completion notifications."""
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)
            logger.debug(f"Subscriber registered: {subscriber.__class__.__name__}")

    def unsubscribe(self, subscriber: CompletionSubscriber) -> None:
        """Unsubscribe from completion notifications."""
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)
            logger.debug(f"Subscriber unregistered: {subscriber.__class__.__name__}")

    async def _notify_subscribers(self, event: CompletionEvent) -> None:
        """Notify all subscribers of completion event."""
        for subscriber in self._subscribers:
            try:
                await subscriber.on_completion_handled(event)
            except Exception as e:
                logger.error(
                    f"Subscriber {subscriber.__class__.__name__} error: {e}",
                    exc_info=True
                )

    # Completion handling (delegates to CompletionRouter)

    async def handle_completion(self, params: dict[str, str]) -> dict[str, Any]:
        """
        Handle authentication completion using completion router.

        Delegates to CompletionRouter which retrieves completion metadata from
        storage and invokes the registered handler. Notifies subscribers of
        the result.

        Args:
            params: Completion parameters (e.g., {"code": "...", "state": "..."})

        Returns:
            Dict with completion result including metadata

        Raises:
            ValueError: If state is invalid or no handler registered
        """
        try:
            result = await self.completion_router.route_completion(self, params)

            state = params.get('state', 'unknown')

            event = CompletionEvent(
                state=state,
                params=params,
                result=result,
                metadata=result,  # Result includes completion metadata
                success=True,
                error=None
            )
            await self._notify_subscribers(event)

            return result

        except Exception as e:
            state = params.get('state', 'unknown')
            event = CompletionEvent(
                state=state,
                params=params,
                result={},
                metadata={},
                success=False,
                error=str(e)
            )
            await self._notify_subscribers(event)
            raise

    # Auth pending state management (delegates to AuthStateStorage)

    async def set_auth_pending(
        self,
        context_id: str,
        server_name: str,
        auth_metadata: dict[str, Any],
        ttl: timedelta | None = None
    ) -> None:
        """
        Signal that authentication is pending for a server.

        Delegates to AuthStateStorage for type-safe state management.

        Args:
            context_id: Context identifier
            server_name: Server name requiring auth
            auth_metadata: Strategy-specific metadata (e.g., authorization_url, state)
            ttl: Optional expiration time
        """
        await self.state_store.mark_auth_pending(
            context_id, server_name, auth_metadata, ttl
        )
        logger.debug(f"Auth pending for {context_id}/{server_name}")

    async def get_auth_pending(
        self,
        context_id: str,
        server_name: str
    ) -> dict[str, Any] | None:
        """
        Get pending authentication metadata for a server.

        Delegates to AuthStateStorage for type-safe state retrieval.

        Args:
            context_id: Context identifier
            server_name: Server name to check

        Returns:
            Auth metadata if pending, None otherwise
        """
        return await self.state_store.get_auth_status(context_id, server_name)

    async def clear_auth_pending(
        self,
        context_id: str,
        server_name: str
    ) -> None:
        """
        Clear pending authentication state.

        Delegates to AuthStateStorage for cleanup.

        Args:
            context_id: Context identifier
            server_name: Server name
        """
        await self.state_store.clear_auth_status(context_id, server_name)
        logger.debug(f"Cleared auth pending for {context_id}/{server_name}")

    async def register_completion_route(
        self,
        routing_key: str,
        handler_name: str,
        storage_namespace: str,
        context_id: str,
        server_name: str,
        routing_param: str = "state",
        handler_kwargs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        ttl: timedelta | None = None
    ) -> None:
        """
        Register completion routing info for stateless execution.

        Delegates to AuthStateStorage for type-safe route registration.

        Args:
            routing_key: Key to route completion (e.g., OAuth state parameter value)
            handler_name: Name of registered completion handler (e.g., "oauth_completion")
            storage_namespace: Storage namespace path for strategy data
            context_id: Context identifier
            server_name: Server name for auth pending cleanup
            routing_param: Query parameter name for routing (default: "state")
            handler_kwargs: Optional kwargs to pass to completion handler (e.g., client_factory)
            metadata: Optional metadata
            ttl: Expiration time
        """
        completion_data = {
            "handler_name": handler_name,
            "storage_namespace": storage_namespace,
            "context_id": context_id,
            "server_name": server_name,
            "routing_param": routing_param,
            "handler_kwargs": handler_kwargs or {},
            **(metadata or {})
        }
        await self.state_store.register_completion_route(
            routing_key, completion_data, ttl
        )
        logger.debug(f"Registered completion route for {routing_key[:8]}...")

    # Endpoint management (delegates to EndpointManager)

    async def get_redirect_uris(self) -> list[str] | None:
        """
        Get OAuth redirect URIs for this coordinator.

        Delegates to EndpointManager. For coordinators with infrastructure
        (e.g., local server), this may start it if not already running.

        Returns:
            List of redirect URIs (e.g., ["http://localhost:8080/callback"])
        """
        if self.endpoint_manager is None:
            return None
        return await self.endpoint_manager.get_redirect_uris()

    async def handle_redirect(
        self,
        authorization_url: str,
        metadata: dict[str, Any]
    ):
        """
        Handle user redirect to authorization URL.

        Args:
            authorization_url: URL to redirect user to
            metadata: Flow metadata (e.g., server_name, state)
        """
        if self.endpoint_manager is None:
            raise RuntimeError("No endpoint manager configured")
        await self.endpoint_manager.initiate_redirect(authorization_url, metadata)

