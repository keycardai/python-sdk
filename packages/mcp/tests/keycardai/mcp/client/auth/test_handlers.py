"""Unit tests for the CompletionHandlerRegistry."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from keycardai.mcp.client.auth.handlers import (
    CompletionHandlerRegistry,
    get_default_handler_registry,
    oauth_completion_handler,
    register_completion_handler,
)
from keycardai.mcp.client.storage import InMemoryBackend, NamespacedStorage


class TestCompletionHandlerRegistry:
    """Test the CompletionHandlerRegistry class."""

    def test_init_creates_empty_registry(self):
        """Test that initialization creates an empty registry."""
        registry = CompletionHandlerRegistry()
        assert registry.list_handlers() == []

    def test_register_as_decorator(self):
        """Test registering a completion handler using decorator syntax."""
        registry = CompletionHandlerRegistry()

        @registry.register("test_completion")
        async def test_handler(coordinator, storage, params):
            return {"success": True}

        assert registry.has("test_completion")
        assert registry.get("test_completion") == test_handler

    def test_register_direct_call(self):
        """Test registering a completion handler via direct call."""
        registry = CompletionHandlerRegistry()

        async def test_handler(coordinator, storage, params):
            return {"success": True}

        registry.register("test_completion", test_handler)

        assert registry.has("test_completion")
        assert registry.get("test_completion") == test_handler

    def test_get_nonexistent_handler_raises_error(self):
        """Test that getting a non-existent handler raises ValueError."""
        registry = CompletionHandlerRegistry()

        with pytest.raises(ValueError, match="Unknown completion handler: nonexistent"):
            registry.get("nonexistent")

    def test_has_returns_false_for_nonexistent(self):
        """Test that has() returns False for non-existent handlers."""
        registry = CompletionHandlerRegistry()
        assert not registry.has("nonexistent")

    def test_list_handlers_returns_all_names(self):
        """Test that list_handlers returns all registered handler names."""
        registry = CompletionHandlerRegistry()

        @registry.register("handler1")
        async def handler1(coordinator, storage, params):
            pass

        @registry.register("handler2")
        async def handler2(coordinator, storage, params):
            pass

        handlers = registry.list_handlers()
        assert len(handlers) == 2
        assert "handler1" in handlers
        assert "handler2" in handlers

    def test_unregister_removes_handler(self):
        """Test that unregister removes a handler."""
        registry = CompletionHandlerRegistry()

        @registry.register("test_completion")
        async def test_handler(coordinator, storage, params):
            pass

        assert registry.has("test_completion")
        registry.unregister("test_completion")
        assert not registry.has("test_completion")

    def test_unregister_nonexistent_is_noop(self):
        """Test that unregistering a non-existent handler doesn't raise error."""
        registry = CompletionHandlerRegistry()
        # Should not raise
        registry.unregister("nonexistent")


class TestDefaultRegistry:
    """Test the default global registry."""

    def test_get_default_handler_registry_returns_singleton(self):
        """Test that get_default_handler_registry returns the same instance."""
        registry1 = get_default_handler_registry()
        registry2 = get_default_handler_registry()
        assert registry1 is registry2

    def test_register_completion_handler_decorator(self):
        """Test registering via global decorator."""
        # Note: We need to be careful not to pollute the global registry
        # but for testing purposes, we'll use a unique name
        test_name = "test_global_completion_unique_54321"

        @register_completion_handler(test_name)
        async def test_handler(coordinator, storage, params):
            return {"test": True}

        registry = get_default_handler_registry()
        assert registry.has(test_name)
        handler = registry.get(test_name)
        assert handler == test_handler

        # Cleanup
        registry.unregister(test_name)

    def test_default_registry_includes_oauth_completion(self):
        """Test that default registry includes oauth_completion handler."""
        registry = get_default_handler_registry()
        assert registry.has("oauth_completion")


class TestBuiltInCompletionHandlers:
    """Test built-in completion handlers."""

    @pytest.mark.asyncio
    async def test_oauth_completion_handler_registered(self):
        """Test that oauth_completion handler is registered."""
        registry = get_default_handler_registry()
        assert registry.has("oauth_completion")
        handler = registry.get("oauth_completion")
        assert callable(handler)

    @pytest.mark.asyncio
    async def test_oauth_completion_handler_validates_params(self):
        """Test that oauth_completion handler validates required parameters."""
        # Create mock dependencies
        coordinator = AsyncMock()
        backend = InMemoryBackend()
        storage = NamespacedStorage(backend, "test")

        # Missing code parameter
        with pytest.raises(ValueError, match="Missing authorization code"):
            await oauth_completion_handler(coordinator, storage, {"state": "test_state"})

        # Missing state parameter
        with pytest.raises(ValueError, match="Missing state"):
            await oauth_completion_handler(coordinator, storage, {"code": "test_code"})

    @pytest.mark.asyncio
    async def test_oauth_completion_handler_missing_pkce_state(self):
        """Test that oauth_completion handler handles missing PKCE state."""
        # Create mock dependencies
        coordinator = AsyncMock()
        backend = InMemoryBackend()
        storage = NamespacedStorage(backend, "test")

        # Call with valid params but no PKCE state in storage
        with pytest.raises(ValueError, match="PKCE state not found or expired"):
            await oauth_completion_handler(
                coordinator,
                storage,
                {"code": "test_code", "state": "test_state"}
            )


class TestClientFactoryManagement:
    """Test client factory management in the registry."""

    def test_set_default_client_factory(self):
        """Test setting a default client factory."""
        registry = CompletionHandlerRegistry()

        def my_factory():
            return "custom_client"

        registry.set_client_factory(my_factory)

        # Should be available for any handler
        factory = registry.get_client_factory("oauth_completion")
        assert factory is not None
        assert factory() == "custom_client"

    def test_set_handler_specific_factory(self):
        """Test setting a factory for a specific handler."""
        registry = CompletionHandlerRegistry()

        def default_factory():
            return "default_client"

        def oauth_factory():
            return "oauth_client"

        registry.set_client_factory(default_factory)
        registry.set_client_factory(oauth_factory, "oauth_completion")

        # OAuth completion should get specific factory
        oauth_fact = registry.get_client_factory("oauth_completion")
        assert oauth_fact() == "oauth_client"

        # Other handlers should get default
        other_fact = registry.get_client_factory("other_handler")
        assert other_fact() == "default_client"

    def test_handler_specific_factory_overrides_default(self):
        """Test that handler-specific factory takes precedence."""
        registry = CompletionHandlerRegistry()

        def default_factory():
            return "default"

        def specific_factory():
            return "specific"

        registry.set_client_factory(default_factory)
        registry.set_client_factory(specific_factory, "test_handler")

        # Specific should override default
        factory = registry.get_client_factory("test_handler")
        assert factory() == "specific"

    def test_get_factory_returns_none_if_not_set(self):
        """Test that get_client_factory returns None if no factory is set."""
        registry = CompletionHandlerRegistry()
        factory = registry.get_client_factory("nonexistent")
        assert factory is None

    def test_clear_client_factories(self):
        """Test clearing all client factories."""
        registry = CompletionHandlerRegistry()

        def factory():
            return "client"

        registry.set_client_factory(factory)
        registry.set_client_factory(factory, "specific")

        # Clear all
        registry.clear_client_factories()

        # All should be None now
        assert registry.get_client_factory("oauth") is None
        assert registry.get_client_factory("specific") is None


class TestCompletionHandlerIntegration:
    """Test completion handler registry integration."""

    @pytest.mark.asyncio
    async def test_handler_can_be_invoked_from_registry(self):
        """Test that a registered handler can be invoked from the registry."""
        registry = CompletionHandlerRegistry()

        # Track if handler was invoked
        invoked = {"called": False}

        @registry.register("test_integration")
        async def test_handler(coordinator, storage, params):
            invoked["called"] = True
            return {"success": True, "params": params}

        # Get and invoke the handler
        handler = registry.get("test_integration")
        result = await handler(
            MagicMock(),  # coordinator
            MagicMock(),  # storage
            {"test": "param"}
        )

        assert invoked["called"]
        assert result["success"]
        assert result["params"]["test"] == "param"

    @pytest.mark.asyncio
    async def test_multiple_handlers_can_be_registered(self):
        """Test that multiple handlers can be registered and invoked independently."""
        registry = CompletionHandlerRegistry()

        results = {}

        @registry.register("handler_a")
        async def handler_a(coordinator, storage, params):
            results["a"] = params
            return {"handler": "a"}

        @registry.register("handler_b")
        async def handler_b(coordinator, storage, params):
            results["b"] = params
            return {"handler": "b"}

        # Invoke both handlers
        retrieved_handler_a = registry.get("handler_a")
        retrieved_handler_b = registry.get("handler_b")

        result_a = await retrieved_handler_a(MagicMock(), MagicMock(), {"data": "a"})
        result_b = await retrieved_handler_b(MagicMock(), MagicMock(), {"data": "b"})

        assert result_a["handler"] == "a"
        assert result_b["handler"] == "b"
        assert results["a"]["data"] == "a"
        assert results["b"]["data"] == "b"


class TestIsolatedRegistryPattern:
    """Test patterns for using isolated registries in tests."""

    @pytest.fixture
    def isolated_registry(self):
        """Create an isolated registry for testing."""
        registry = CompletionHandlerRegistry()

        # Register test handler
        @registry.register("test_oauth_completion")
        async def test_oauth_completion(coordinator, storage, params):
            return {"success": True, "test": True}

        return registry

    @pytest.mark.asyncio
    async def test_isolated_registry_doesnt_affect_global(self, isolated_registry):
        """Test that isolated registry doesn't pollute global registry."""
        # Get default registry
        default_registry = get_default_handler_registry()

        # Isolated registry should have test handler
        assert isolated_registry.has("test_oauth_completion")

        # Default registry should not have test handler
        assert not default_registry.has("test_oauth_completion")

    @pytest.mark.asyncio
    async def test_coordinator_with_isolated_registry(self, isolated_registry):
        """Test creating coordinator with isolated registry."""
        from keycardai.mcp.client.auth.coordinators.base import AuthCoordinator
        from keycardai.mcp.client.storage import InMemoryBackend

        # Create a concrete coordinator subclass for testing
        class TestCoordinator(AuthCoordinator):
            @property
            def endpoint_type(self) -> str:
                return "test"

        # Create coordinator with isolated registry
        backend = InMemoryBackend()
        coordinator = TestCoordinator(
            backend=backend,
            handler_registry=isolated_registry
        )

        # Coordinator should use the isolated registry
        # (indirectly via completion_router)
        assert coordinator.completion_router.handler_registry["test_oauth_completion"]

