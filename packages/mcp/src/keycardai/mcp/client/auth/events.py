"""
Subscriber protocol for authentication completion notifications.

Defines the interface and data structures for receiving notifications
when authentication completions are handled.
"""
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class CompletionEvent:
    """
    Event data for authentication completion notifications.

    This dataclass encapsulates all relevant information about a
    completion event, allowing subscribers to react appropriately.

    Attributes:
        state: The state parameter from the completion
        params: All completion parameters received
        result: The result returned by the completion handler
        metadata: Additional metadata registered with the completion
        success: Whether the completion was handled successfully
        error: Error message if completion handling failed (None on success)
    """
    state: str
    params: dict[str, str]
    result: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str | None = None


class CompletionSubscriber(Protocol):
    """
    Protocol for receiving authentication completion notifications.

    Classes implementing this protocol can subscribe to an AuthCoordinator
    to receive notifications when completions are handled. This allows for
    decoupled event handling, logging, analytics, UI updates, etc.

    Example:
        class MySubscriber:
            async def on_completion_handled(self, event: CompletionEvent) -> None:
                if event.success:
                    print(f"Auth completed for state {event.state}")
                else:
                    print(f"Auth failed: {event.error}")

        subscriber = MySubscriber()
        coordinator.subscribe(subscriber)
    """

    async def on_completion_handled(self, event: CompletionEvent) -> None:
        """
        Called when an authentication completion has been handled.

        This method is invoked by the AuthCoordinator after a completion
        has been processed, whether successful or not. Implementations
        should not raise exceptions, as they could interfere with the
        completion flow.

        Args:
            event: Completion event containing all relevant information

        Note:
            Subscribers should handle their own errors gracefully and
            avoid raising exceptions. Any exceptions raised will be
            logged but will not affect the completion handling process.
        """
        ...

