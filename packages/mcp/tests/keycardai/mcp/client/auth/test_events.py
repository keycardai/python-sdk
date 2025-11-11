"""Tests for callback subscriber functionality.

Tests the subscriber protocol implementation, event notifications,
and integration with the AuthCoordinator.
"""
import pytest

from keycardai.mcp.client.auth.coordinators.base import AuthCoordinator
from keycardai.mcp.client.auth.events import CompletionEvent


class MockAuthCoordinator(AuthCoordinator):
    """Mock coordinator for testing subscriber functionality."""

    @property
    def endpoint_type(self) -> str:
        """Return test endpoint type."""
        return "test"

    async def get_redirect_uris(self) -> list[str] | None:
        return ["http://localhost:8080/callback"]

    async def start(self):
        pass

    async def shutdown(self):
        pass

    async def handle_redirect(self, authorization_url: str, metadata: dict):
        pass


class TestCompletionEvent:
    """Test CompletionEvent dataclass."""

    def test_callback_event_creation_success(self):
        """Test creating a successful callback event."""
        event = CompletionEvent(
            state="abc123",
            params={"code": "xyz", "state": "abc123"},
            result={"access_token": "token123"},
            metadata={"server_name": "test"},
            success=True,
            error=None
        )

        assert event.state == "abc123"
        assert event.params == {"code": "xyz", "state": "abc123"}
        assert event.result == {"access_token": "token123"}
        assert event.metadata == {"server_name": "test"}
        assert event.success is True
        assert event.error is None

    def test_callback_event_creation_failure(self):
        """Test creating a failed callback event."""
        event = CompletionEvent(
            state="abc123",
            params={"state": "abc123"},
            result={},
            metadata={"server_name": "test"},
            success=False,
            error="Invalid callback parameters"
        )

        assert event.state == "abc123"
        assert event.success is False
        assert event.error == "Invalid callback parameters"
        assert event.result == {}

    def test_callback_event_default_values(self):
        """Test default values for optional fields."""
        event = CompletionEvent(
            state="abc123",
            params={},
            result={}
        )

        assert event.metadata == {}
        assert event.success is True
        assert event.error is None


class TestSubscriberManagement:
    """Test subscriber registration and management."""

    @pytest.mark.asyncio
    async def test_subscribe_adds_subscriber(self):
        """Test that subscribe adds a subscriber to the list."""
        coordinator = MockAuthCoordinator()

        class TestSubscriber:
            async def on_completion_handled(self, event: CompletionEvent) -> None:
                pass

        subscriber = TestSubscriber()
        coordinator.subscribe(subscriber)

        assert subscriber in coordinator._subscribers
        assert len(coordinator._subscribers) == 1

    @pytest.mark.asyncio
    async def test_subscribe_idempotent(self):
        """Test that subscribing the same subscriber twice doesn't duplicate."""
        coordinator = MockAuthCoordinator()

        class TestSubscriber:
            async def on_completion_handled(self, event: CompletionEvent) -> None:
                pass

        subscriber = TestSubscriber()
        coordinator.subscribe(subscriber)
        coordinator.subscribe(subscriber)  # Subscribe again

        assert len(coordinator._subscribers) == 1

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_subscriber(self):
        """Test that unsubscribe removes a subscriber."""
        coordinator = MockAuthCoordinator()

        class TestSubscriber:
            async def on_completion_handled(self, event: CompletionEvent) -> None:
                pass

        subscriber = TestSubscriber()
        coordinator.subscribe(subscriber)
        assert len(coordinator._subscribers) == 1

        coordinator.unsubscribe(subscriber)
        assert len(coordinator._subscribers) == 0
        assert subscriber not in coordinator._subscribers

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent_subscriber(self):
        """Test that unsubscribing a non-registered subscriber doesn't raise error."""
        coordinator = MockAuthCoordinator()

        class TestSubscriber:
            async def on_completion_handled(self, event: CompletionEvent) -> None:
                pass

        subscriber = TestSubscriber()
        # Should not raise an error
        coordinator.unsubscribe(subscriber)
        assert len(coordinator._subscribers) == 0

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        """Test managing multiple subscribers."""
        coordinator = MockAuthCoordinator()

        class Subscriber1:
            async def on_completion_handled(self, event: CompletionEvent) -> None:
                pass

        class Subscriber2:
            async def on_completion_handled(self, event: CompletionEvent) -> None:
                pass

        sub1 = Subscriber1()
        sub2 = Subscriber2()

        coordinator.subscribe(sub1)
        coordinator.subscribe(sub2)

        assert len(coordinator._subscribers) == 2
        assert sub1 in coordinator._subscribers
        assert sub2 in coordinator._subscribers

        coordinator.unsubscribe(sub1)
        assert len(coordinator._subscribers) == 1
        assert sub2 in coordinator._subscribers


# NOTE: Callback notification and integration tests have been removed.
# The storage-based callback mechanism is tested through:
# - test_strategies.py: Tests unified OAuthStrategy with callback routes
# - test_stateless_callbacks.py: Tests callback registry and invocation
# - Integration tests: Test end-to-end subscriber notifications in real scenarios
#
# The subscriber protocol itself (subscribe/unsubscribe) is tested in TestSubscriberManagement above.

