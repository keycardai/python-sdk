"""Auth completion routing and handlers."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from httpx import AsyncClient

from ..logging_config import get_logger
from ..storage import NamespacedStorage
from .storage_facades import AuthStateStorage

if TYPE_CHECKING:
    from .coordinators.base import AuthCoordinator

logger = get_logger(__name__)


class CompletionRouter:
    """
    Routes auth completions to registered handlers.

    Handles both stateful (in-memory) and stateless (storage-based) routing.
    Retrieves completion metadata from storage, invokes the appropriate handler,
    and manages cleanup.

    Responsibilities:
    - Retrieve completion routing metadata from storage
    - Look up and invoke registered completion handlers
    - Navigate to correct storage namespace for handler
    - Schedule cleanup of routing metadata
    """

    def __init__(
        self,
        state_storage: AuthStateStorage,
        handler_registry: dict[str, Any]
    ):
        """
        Initialize completion router.

        Args:
            state_storage: Auth state storage facade for completion routing
            handler_registry: Dict mapping handler names to handler functions
        """
        self.state_storage = state_storage
        self.handler_registry = handler_registry

    async def route_completion(
        self,
        coordinator: "AuthCoordinator",
        params: dict[str, str]
    ) -> dict[str, Any]:
        """
        Route completion to appropriate handler.

        Retrieves routing metadata from storage, invokes the registered handler,
        and schedules cleanup. The coordinator is passed to handlers for context
        creation and other coordinator operations.

        Args:
            coordinator: AuthCoordinator instance
            params: Completion parameters (e.g., {"code": "...", "state": "..."})

        Returns:
            Handler result dict merged with completion metadata

        Raises:
            ValueError: If state is missing, invalid, or handler not found
        """
        state = params.get('state')
        if not state:
            raise ValueError("Missing state parameter in completion")

        completion_metadata = await self.state_storage.get_completion_route(state)
        if not completion_metadata:
            raise ValueError("Invalid or expired state parameter")

        result = await self._invoke_handler(
            coordinator,
            state,
            params,
            completion_metadata
        )

        # Schedule cleanup as background task (don't block HTTP response)
        # Note: Cleanup happens asynchronously after response is sent
        asyncio.create_task(self._cleanup_completion_route(state))

        logger.info(f"Completion handled successfully for state: {state[:8]}...")

        # Merge result with completion metadata for backward compatibility
        return {**result, **completion_metadata}

    async def _invoke_handler(
        self,
        coordinator: "AuthCoordinator",
        state: str,
        params: dict[str, str],
        completion_metadata: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Invoke registered completion handler with proper context.

        Args:
            coordinator: AuthCoordinator instance
            state: OAuth state parameter
            params: Completion parameters
            completion_metadata: Routing metadata from storage

        Returns:
            Handler result dict

        Raises:
            ValueError: If handler name not found in registry
        """
        handler_name = completion_metadata.get("handler_name") or completion_metadata.get("callback_name")
        storage_namespace = completion_metadata["storage_namespace"]
        context_id = completion_metadata["context_id"]

        context = coordinator.create_context(context_id)
        strategy_storage = self._navigate_to_namespace(
            context.storage,
            storage_namespace,
            context_id
        )

        if handler_name not in self.handler_registry:
            raise ValueError(f"Unknown completion handler: {handler_name}")

        handler = self.handler_registry[handler_name]

        handler_kwargs = completion_metadata.get("handler_kwargs") or completion_metadata.get("callback_kwargs", {})

        result = await handler(
            coordinator=coordinator,
            storage=strategy_storage,
            params=params,
            **handler_kwargs
        )

        return result

    def _navigate_to_namespace(
        self,
        storage: NamespacedStorage,
        full_namespace: str,
        context_id: str
    ) -> NamespacedStorage:
        """
        Navigate from context storage to strategy storage namespace.

        Parses the full namespace path and navigates through the hierarchy
        to reach the strategy's storage namespace.

        Args:
            storage: Context storage (at "client:{context_id}" level)
            full_namespace: Full namespace path (e.g., "client:user:server:slack:connection:oauth")
            context_id: Context identifier

        Returns:
            Strategy storage at the full namespace
        """
        prefix = f"client:{context_id}:"
        if full_namespace.startswith(prefix):
            remaining_path = full_namespace[len(prefix):]
            for part in remaining_path.split(":"):
                if part:
                    storage = storage.get_namespace(part)
        return storage

    async def _cleanup_completion_route(self, state: str) -> None:
        """
        Clean up completion routing metadata as background task.

        This runs asynchronously after the HTTP response is sent to avoid
        blocking the completion response. Handles cancellation gracefully.

        Args:
            state: OAuth state parameter to clean up
        """
        try:
            await self.state_storage.delete_completion_route(state)
            logger.debug(f"Cleaned up completion route for state: {state[:8]}...")
        except asyncio.CancelledError:
            # This is expected if the task is cancelled during shutdown
            logger.debug(f"Completion cleanup cancelled for state: {state[:8]}...")
        except Exception as e:
            # Log but don't propagate errors from background cleanup
            logger.warning(f"Failed to cleanup completion route for state {state[:8]}...: {e}")



# ============================================================================
# Completion Handler Registry and Built-in Handlers
# ============================================================================
# This section provides a registry system for auth completion handlers that
# can be invoked to complete authentication flows, including in stateless
# environments where callbacks are handled by different process invocations.



# Type alias for completion handler functions
CompletionHandlerFunc = Callable[
    ["AuthCoordinator", "NamespacedStorage", dict[str, str]],
    Awaitable[dict[str, Any]]
]

# Type for client factory functions (commonly used in OAuth completions)
ClientFactory = Callable[[], "AsyncClient"]


class CompletionHandlerRegistry:
    """
    Registry of auth completion handlers.

    Manages registration and discovery of completion handler functions that
    can be used to complete authentication flows.

    Example:
        >>> registry = CompletionHandlerRegistry()
        >>> @registry.register("oauth_completion")
        >>> async def oauth_completion(coordinator, storage, params):
        >>>     # Handle OAuth completion
        >>>     return {"success": True}
        >>>
        >>> handler = registry.get("oauth_completion")
        >>> result = await handler(coordinator, storage, params)
    """

    def __init__(self):
        """Initialize the completion handler registry."""
        self._handlers: dict[str, CompletionHandlerFunc] = {}
        self._client_factories: dict[str, ClientFactory] = {}
        self._default_client_factory: ClientFactory | None = None

    def register(
        self,
        name: str,
        handler: CompletionHandlerFunc | None = None
    ) -> CompletionHandlerFunc | Callable[[CompletionHandlerFunc], CompletionHandlerFunc]:
        """
        Register a completion handler.

        Can be used as a decorator or called directly.

        Args:
            name: Unique name for the completion handler
            handler: Optional handler function (if not using as decorator)

        Returns:
            The handler function (for decorator chaining) or decorator function

        Example:
            >>> # As decorator
            >>> @registry.register("my_completion")
            >>> async def my_completion(coordinator, storage, params):
            >>>     return {"success": True}
            >>>
            >>> # Direct registration
            >>> registry.register("my_completion", my_completion_func)
        """
        if handler is not None:
            self._handlers[name] = handler
            logger.debug(f"Registered completion handler: {name}")
            return handler

        def decorator(func: CompletionHandlerFunc) -> CompletionHandlerFunc:
            self._handlers[name] = func
            logger.debug(f"Registered completion handler: {name}")
            return func

        return decorator

    def get(self, name: str) -> CompletionHandlerFunc:
        """
        Get a registered completion handler by name.

        Args:
            name: Name of the completion handler

        Returns:
            The completion handler function

        Raises:
            ValueError: If handler is not registered
        """
        if name not in self._handlers:
            raise ValueError(
                f"Unknown completion handler: {name}. "
                f"Available handlers: {', '.join(self._handlers.keys())}"
            )
        return self._handlers[name]

    def has(self, name: str) -> bool:
        """
        Check if a completion handler is registered.

        Args:
            name: Name of the completion handler

        Returns:
            True if handler is registered, False otherwise
        """
        return name in self._handlers

    def list_handlers(self) -> list[str]:
        """
        List all registered completion handler names.

        Returns:
            List of handler names
        """
        return list(self._handlers.keys())

    def unregister(self, name: str) -> None:
        """
        Unregister a completion handler.

        Args:
            name: Name of the handler to unregister
        """
        if name in self._handlers:
            del self._handlers[name]
            logger.debug(f"Unregistered completion handler: {name}")

    def set_client_factory(
        self,
        factory: ClientFactory,
        handler_name: str | None = None
    ) -> None:
        """
        Set a client factory for completion handlers.

        Args:
            factory: Factory function that returns an HTTP client (e.g., AsyncClient)
            handler_name: Optional handler name to set factory for specific handler.
                         If None, sets as default factory for all handlers.

        Example:
            >>> from httpx import AsyncClient
            >>> registry = CompletionHandlerRegistry()
            >>>
            >>> # Set default factory for all handlers
            >>> def my_factory():
            >>>     return AsyncClient(timeout=30.0, verify=True)
            >>> registry.set_client_factory(my_factory)
            >>>
            >>> # Set factory for specific handler
            >>> def oauth_factory():
            >>>     return AsyncClient(timeout=60.0)
            >>> registry.set_client_factory(oauth_factory, "oauth_completion")
        """
        if handler_name is None:
            self._default_client_factory = factory
            logger.debug("Set default client factory for all completion handlers")
        else:
            self._client_factories[handler_name] = factory
            logger.debug(f"Set client factory for completion handler: {handler_name}")

    def get_client_factory(self, handler_name: str) -> ClientFactory | None:
        """
        Get the client factory for a specific completion handler.

        Args:
            handler_name: Name of the completion handler

        Returns:
            Client factory function or None if not set.
            Checks handler-specific factory first, then falls back to default.

        Example:
            >>> factory = registry.get_client_factory("oauth_completion")
            >>> if factory:
            >>>     client = factory()
        """
        if handler_name in self._client_factories:
            return self._client_factories[handler_name]

        return self._default_client_factory

    def clear_client_factories(self) -> None:
        """
        Clear all client factories (both default and handler-specific).

        Useful for testing or reconfiguration.
        """
        self._client_factories.clear()
        self._default_client_factory = None
        logger.debug("Cleared all client factories")


# Global default registry (for convenience)
_default_registry = CompletionHandlerRegistry()


def get_default_handler_registry() -> CompletionHandlerRegistry:
    """
    Get the default global handler registry.

    Returns:
        The global CompletionHandlerRegistry instance
    """
    return _default_registry


def _setup_default_factory() -> None:
    """Set up default AsyncClient factory for completion handlers."""
    def default_factory() -> AsyncClient:
        """Default HTTP client factory."""
        return AsyncClient()

    _default_registry.set_client_factory(default_factory)


_setup_default_factory()


def register_completion_handler(
    name: str
) -> Callable[[CompletionHandlerFunc], CompletionHandlerFunc]:
    """
    Decorator to register a completion handler in the default registry.

    This is the main decorator used throughout the SDK to mark functions
    as available for auth completion.

    Args:
        name: Unique name for the completion handler

    Returns:
        Decorator function

    Example:
        >>> @register_completion_handler("oauth_completion")
        >>> async def oauth_completion(coordinator, storage, params):
        >>>     # Handle OAuth completion
        >>>     return {"success": True}
    """
    return _default_registry.register(name)


# ===== Built-in Handlers =====


@register_completion_handler("oauth_completion")
async def oauth_completion_handler(
    coordinator: "AuthCoordinator",
    storage: "NamespacedStorage",
    params: dict[str, str],
    client_factory: Callable[[], "AsyncClient"] | None = None,
    run_cleanup_in_background: bool = True
) -> dict[str, Any]:
    """
    Handle OAuth authorization code completion.

    This unified completion handler works with any coordinator by retrieving all necessary
    state from storage.

    Steps:
    1. Extract code and state from params
    2. Load PKCE state from storage
    3. Exchange authorization code for tokens
    4. Store tokens in strategy storage
    5. Clear pending auth state
    6. Return success result

    Args:
        coordinator: Auth coordinator (for cleanup)
        storage: Strategy's namespaced storage
        params: Completion parameters (e.g., {"code": "...", "state": "..."})
        client_factory: Optional factory function that returns an AsyncClient instance.
                       If None, uses default factory that creates AsyncClient()
        run_cleanup_in_background: If True (default), cleanup tasks run asynchronously
                                  after response. If False, cleanup runs synchronously.
                                  Use False in unit tests to ensure cleanup completes.

    Returns:
        Result dict with success status and metadata

    Raises:
        ValueError: If required parameters are missing or state is invalid

    Note:
        For LocalAuthCoordinator, storage is typically InMemoryBackend (instant lookup).
        For RemoteAuthCoordinator, storage is typically Redis or DynamoDB.

    Example:
        # Standard usage (automatic via coordinator)
        # When completion arrives, coordinator invokes this handler

        # Custom HTTP client (for serverless with special requirements):
        @register_completion_handler("my_oauth")
        async def my_completion(coordinator, storage, params, **kwargs):
            def my_factory():
                return AsyncClient(timeout=30.0, verify=True)
            return await oauth_completion_handler(
                coordinator, storage, params, client_factory=my_factory
            )
    """
    code = params.get("code")
    state = params.get("state")

    if not code:
        raise ValueError("Missing authorization code in completion")
    if not state:
        raise ValueError("Missing state in completion")

    logger.info(f"Handling OAuth completion for state: {state}")

    pkce_state = await storage.get(f"_pkce_state:{state}")
    if not pkce_state:
        raise ValueError("PKCE state not found or expired")

    # Resolve client factory with priority:
    # 1. Explicitly passed factory
    # 2. Registry factory (handler-specific or default)
    if client_factory is None:
        client_factory = _default_registry.get_client_factory("oauth_completion")

    if client_factory is None:
        raise ValueError(
            "No HTTP client factory configured. "
            "Please configure one using set_client_factory() or ensure httpx is installed."
        )

    async with client_factory() as client:
        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pkce_state["redirect_uri"],
            "client_id": pkce_state["client_id"],
            "code_verifier": pkce_state["code_verifier"],
        }
        if pkce_state.get("resource_url"):
            token_data["resource"] = pkce_state["resource_url"]

        response = await client.post(
            pkce_state["token_endpoint"],
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

        if response.status_code != 200:
            # Don't expose full error response - may contain sensitive details
            raise ValueError(f"Token exchange failed with status {response.status_code}")

        tokens = response.json()

    await storage.set("tokens", tokens)
    logger.info("Tokens stored successfully")

    server_name = pkce_state.get("server_name", "unknown")

    namespace_parts = storage._namespace.split(":")
    context_id = namespace_parts[1] if len(namespace_parts) > 1 else "unknown"

    # Cleanup: Run as background task (async) or synchronously based on parameter
    # Note: completion metadata cleanup is handled by coordinator
    if run_cleanup_in_background:
        asyncio.create_task(_cleanup_oauth_completion_state(
            storage, coordinator, state, context_id, server_name
        ))
    else:
        await _cleanup_oauth_completion_state(
            storage, coordinator, state, context_id, server_name
        )

    logger.info(f"OAuth completion completed for {server_name}")

    return {"success": True, "server_name": server_name}


async def _cleanup_oauth_completion_state(
    storage: "NamespacedStorage",
    coordinator: "AuthCoordinator",
    state: str,
    context_id: str,
    server_name: str
) -> None:
    """
    Clean up OAuth completion state as a background task.

    This runs asynchronously after the HTTP response is sent to avoid
    blocking the completion response.

    Args:
        storage: Strategy storage
        coordinator: Auth coordinator
        state: OAuth state parameter
        context_id: Context identifier
        server_name: Server name
    """
    try:
        await storage.delete(f"_pkce_state:{state}")
        await coordinator.clear_auth_pending(context_id=context_id, server_name=server_name)
        logger.debug(f"Cleaned up OAuth completion state for {server_name}")
    except asyncio.CancelledError:
        logger.debug(f"OAuth completion cleanup cancelled for {server_name}")
    except Exception as e:
        # Log but don't propagate errors from background cleanup
        logger.warning(f"Failed to cleanup OAuth completion state for {server_name}: {e}")

