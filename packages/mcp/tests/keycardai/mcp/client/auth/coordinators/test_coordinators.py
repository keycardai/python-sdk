"""Unit tests for the AuthCoordinator base class.

This module tests the AuthCoordinator abstract base class, focusing on:
- Initialization and storage handling
- Context creation with proper namespacing
- Callback registration and routing
- Callback handling with cleanup and error cases
- Abstract method enforcement
- Injectable completion handler registry

Note: Concrete implementations (LocalAuthCoordinator, StarletteAuthCoordinator)
are tested separately.
"""

from typing import Any

import pytest

from keycardai.mcp.client.auth.coordinators.base import AuthCoordinator
from keycardai.mcp.client.context import Context
from keycardai.mcp.client.storage import InMemoryBackend, NamespacedStorage


# Concrete implementation for testing the abstract base class
class ConcreteAuthCoordinator(AuthCoordinator):
    """Minimal concrete implementation for testing abstract base."""

    def __init__(self, backend: InMemoryBackend | None = None):
        super().__init__(backend)
        self.start_called = False
        self.shutdown_called = False
        self.handle_redirect_calls = []

    @property
    def endpoint_type(self) -> str:
        """Return test endpoint type."""
        return "test"

    async def get_callback_uris(self) -> list[str] | None:
        """Return test callback URIs."""
        return ["http://localhost:8080/callback"]

    async def start(self):
        """Track start calls."""
        self.start_called = True

    async def shutdown(self):
        """Track shutdown calls."""
        self.shutdown_called = True

    async def handle_redirect(self, authorization_url: str, metadata: dict[str, Any]):
        """Track redirect calls."""
        self.handle_redirect_calls.append((authorization_url, metadata))


class TestAuthCoordinatorInitialization:
    """Test AuthCoordinator initialization with various configurations."""

    def test_default_initialization_creates_storage(self):
        """Test that coordinator creates default storage when none provided."""
        coordinator = ConcreteAuthCoordinator()

        assert coordinator.storage is not None
        assert isinstance(coordinator.storage, NamespacedStorage)

    def test_initialization_with_custom_storage(self):
        """Test that coordinator uses provided backend."""
        custom_backend = InMemoryBackend()
        coordinator = ConcreteAuthCoordinator(backend=custom_backend)

        assert coordinator.storage is not None
        assert isinstance(coordinator.storage, NamespacedStorage)

    def test_cannot_instantiate_abstract_coordinator(self):
        """Test that AuthCoordinator cannot be instantiated directly."""
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            AuthCoordinator()  # type: ignore


class TestAuthCoordinatorContextCreation:
    """Test AuthCoordinator context creation and namespacing."""

    def test_create_context_returns_context_instance(self):
        """Test that create_context returns a Context instance."""
        coordinator = ConcreteAuthCoordinator()
        context = coordinator.create_context("user:alice")

        assert isinstance(context, Context)

    def test_create_context_sets_correct_id(self):
        """Test that created context has correct identifier."""
        coordinator = ConcreteAuthCoordinator()
        context = coordinator.create_context("user:alice")

        assert context.id == "user:alice"

    def test_create_context_links_coordinator(self):
        """Test that created context references the coordinator."""
        coordinator = ConcreteAuthCoordinator()
        context = coordinator.create_context("user:alice")

        assert context.coordinator is coordinator

    def test_create_context_creates_namespaced_storage(self):
        """Test that created context has properly namespaced storage."""
        coordinator = ConcreteAuthCoordinator()
        context = coordinator.create_context("user:alice")

        # Storage should be a namespace of the coordinator's storage
        assert context.storage is not None
        assert context.storage is not coordinator.storage

    def test_create_context_storage_isolation(self):
        """Test that different contexts have isolated storage."""
        coordinator = ConcreteAuthCoordinator()
        context_alice = coordinator.create_context("user:alice")
        context_bob = coordinator.create_context("user:bob")

        # Storage instances should be different
        assert context_alice.storage is not context_bob.storage

    @pytest.mark.asyncio
    async def test_create_context_storage_writes_are_isolated(self):
        """Test that writes to one context don't affect another."""
        coordinator = ConcreteAuthCoordinator()
        context_alice = coordinator.create_context("user:alice")
        context_bob = coordinator.create_context("user:bob")

        # Write to Alice's storage
        await context_alice.storage.set("key", "alice_value")

        # Bob's storage should not have this key
        bob_value = await context_bob.storage.get("key")
        assert bob_value is None

        # Alice's storage should have the value
        alice_value = await context_alice.storage.get("key")
        assert alice_value == "alice_value"

    def test_create_context_is_idempotent(self):
        """Test that creating contexts with same ID returns the same instance (idempotent)."""
        coordinator = ConcreteAuthCoordinator()
        context1 = coordinator.create_context("user:alice")
        context2 = coordinator.create_context("user:alice")

        # Should return the same Context instance
        assert context1 is context2
        # With same ID
        assert context1.id == context2.id
        assert context2.id == "user:alice"

    def test_create_context_caches_multiple_contexts(self):
        """Test that coordinator caches multiple different contexts."""
        coordinator = ConcreteAuthCoordinator()

        # Create multiple contexts
        context_alice = coordinator.create_context("user:alice")
        context_bob = coordinator.create_context("user:bob")
        context_task = coordinator.create_context("task:123")

        # Should have 3 cached contexts
        assert len(coordinator._contexts) == 3

        # Retrieving same contexts should return cached instances
        assert coordinator.create_context("user:alice") is context_alice
        assert coordinator.create_context("user:bob") is context_bob
        assert coordinator.create_context("task:123") is context_task

        # Cache size should remain the same
        assert len(coordinator._contexts) == 3

    def test_create_context_with_various_id_formats(self):
        """Test context creation with different ID formats."""
        coordinator = ConcreteAuthCoordinator()

        test_ids = [
            "user:alice",
            "session:xyz123",
            "task:456",
            "tenant:acme_corp",
            "simple_id",
            "id-with-dashes",
            "id_with_underscores",
        ]

        for context_id in test_ids:
            context = coordinator.create_context(context_id)
            assert context.id == context_id


class TestAuthCoordinatorIntegrationScenarios:
    """Test complex integration scenarios with AuthCoordinator."""

    @pytest.mark.asyncio
    async def test_context_isolation_with_shared_coordinator(self):
        """Test that contexts created by same coordinator have isolated storage."""
        coordinator = ConcreteAuthCoordinator()

        # Create multiple contexts
        context1 = coordinator.create_context("user:alice")
        context2 = coordinator.create_context("user:bob")
        context3 = coordinator.create_context("task:123")

        # Write to each context's storage
        await context1.storage.set("token", "alice_token")
        await context2.storage.set("token", "bob_token")
        await context3.storage.set("token", "task_token")

        # Verify isolation
        assert await context1.storage.get("token") == "alice_token"
        assert await context2.storage.get("token") == "bob_token"
        assert await context3.storage.get("token") == "task_token"


class TestAuthCoordinatorWithIsolatedRegistry:
    """Test AuthCoordinator with isolated completion handler registry."""

    def test_coordinator_with_isolated_registry(self, isolated_completion_registry):
        """Test creating coordinator with isolated registry."""
        backend = InMemoryBackend()
        coordinator = ConcreteAuthCoordinator(backend)

        # By default, coordinator uses global registry
        # In production code, you would pass the isolated registry:
        # coordinator = ConcreteAuthCoordinator(backend, handler_registry=isolated_completion_registry)

        assert coordinator.completion_router is not None

    def test_coordinator_uses_default_registry_when_none_provided(self):
        """Test that coordinator uses default registry when none provided."""
        from keycardai.mcp.client.auth.handlers import (
            get_default_handler_registry,
        )

        coordinator = ConcreteAuthCoordinator()

        # Coordinator should have used default registry
        default_registry = get_default_handler_registry()
        # The handler registry is accessed via completion_router
        assert coordinator.completion_router.handler_registry == default_registry._handlers

