"""Bridge adapter between Keycard AgentExecutor and A2A AgentExecutor protocols.

This module provides a bridge that allows Keycard's simple synchronous executor
interface to work with the A2A SDK's event-driven asynchronous executor interface.

The bridge enables dual endpoint support:
- Custom /invoke endpoint (simple, synchronous)
- A2A JSONRPC endpoint (standards-compliant, event-driven)

Both endpoints can use the same underlying agent implementation.
"""

import logging
import uuid
from typing import Any

from a2a.server.agent_execution import AgentExecutor as A2AAgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Message,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)

from ..server.executor import AgentExecutor as KeycardExecutor

logger = logging.getLogger(__name__)


class KeycardToA2AExecutorBridge(A2AAgentExecutor):
    """Bridge adapter from Keycard AgentExecutor to A2A AgentExecutor.

    This bridge allows Keycard's simple executor interface to be used with
    A2A's event-driven JSONRPC protocol. It handles:

    1. Converting A2A RequestContext to Keycard task/inputs format
    2. Calling the synchronous Keycard executor
    3. Publishing the result as an A2A Task event
    4. Managing delegation tokens in context

    Args:
        keycard_executor: The Keycard executor to wrap

    Example:
        >>> from keycardai.agents.server.executor import SimpleExecutor
        >>> keycard_executor = SimpleExecutor()
        >>> a2a_executor = KeycardToA2AExecutorBridge(keycard_executor)
        >>>
        >>> # Now can be used with A2A DefaultRequestHandler
        >>> from a2a.server.request_handlers import DefaultRequestHandler
        >>> from a2a.server.tasks import InMemoryTaskStore
        >>> handler = DefaultRequestHandler(
        ...     agent_executor=a2a_executor,
        ...     task_store=InMemoryTaskStore()
        ... )
    """

    def __init__(self, keycard_executor: KeycardExecutor):
        """Initialize the bridge.

        Args:
            keycard_executor: Keycard executor implementing execute(task, inputs)
        """
        self.keycard_executor = keycard_executor

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Execute the agent using Keycard executor and publish A2A events.

        This method:
        1. Extracts task description and inputs from RequestContext
        2. Sets delegation token if available
        3. Calls Keycard executor synchronously
        4. Publishes result as A2A Task event

        Args:
            context: A2A request context with message and task info
            event_queue: Queue to publish task events to

        Raises:
            Exception: If execution fails
        """
        try:
            # Extract task and inputs from A2A context
            task_description = self._extract_task_from_context(context)
            inputs = self._extract_inputs_from_context(context)

            logger.info(
                f"Bridge executing task_id={context.task_id}: {task_description[:100]}"
            )

            # Set delegation token if executor supports it
            # Note: Token would need to be passed via context metadata
            if hasattr(self.keycard_executor, "set_token_for_delegation"):
                # Try to extract token from context metadata
                token = context.metadata.get("access_token")
                if token:
                    self.keycard_executor.set_token_for_delegation(token)

            # Execute synchronously (Keycard executors are sync)
            result = self.keycard_executor.execute(
                task=task_description,
                inputs=inputs,
            )

            logger.info(f"Bridge execution completed for task_id={context.task_id}")

            # Convert result to A2A Task and publish
            task_event = self._create_task_event(
                task_id=context.task_id or "unknown",
                context_id=context.context_id or "unknown",
                result=result,
                original_message=context.message,
            )

            await event_queue.enqueue_event(task_event)

        except Exception as e:
            logger.error(
                f"Bridge execution failed for task_id={context.task_id}: {e}",
                exc_info=True,
            )

            # Publish failed task event
            failed_task = self._create_failed_task_event(
                task_id=context.task_id or "unknown",
                context_id=context.context_id or "unknown",
                error=str(e),
                original_message=context.message,
            )

            await event_queue.enqueue_event(failed_task)

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Handle task cancellation.

        Keycard executors don't currently support cancellation, so this
        publishes a canceled task status.

        Args:
            context: A2A request context
            event_queue: Queue to publish cancellation event to
        """
        logger.info(f"Bridge cancelling task_id={context.task_id}")

        # Create canceled task event
        canceled_task = Task(
            id=context.task_id or "unknown",
            context_id=context.context_id or "unknown",
            status=TaskStatus(
                state=TaskState.canceled,
            ),
            history=[],
            artifacts=[],
        )

        await event_queue.enqueue_event(canceled_task)

    def _extract_task_from_context(self, context: RequestContext) -> str | dict[str, Any]:
        """Extract task description from A2A RequestContext.

        Args:
            context: A2A request context

        Returns:
            Task description as string or dict
        """
        # Use the convenience method to get user input text
        user_input = context.get_user_input()

        if user_input:
            return user_input

        # Fallback: extract from message parts manually
        if context.message and context.message.parts:
            parts = []
            for part in context.message.parts:
                if isinstance(part, TextPart) and hasattr(part, "text"):
                    parts.append(part.text)
                elif isinstance(part, dict) and "text" in part:
                    parts.append(part["text"])

            if parts:
                return "\n".join(parts)

        # Final fallback
        return "No task description provided"

    def _extract_inputs_from_context(
        self, context: RequestContext
    ) -> dict[str, Any] | None:
        """Extract additional inputs from A2A RequestContext metadata.

        Args:
            context: A2A request context

        Returns:
            Inputs dictionary or None
        """
        # Extract from metadata if available
        metadata = context.metadata
        if metadata:
            # Return metadata as inputs (excluding internal fields)
            return {
                k: v
                for k, v in metadata.items()
                if not k.startswith("_")
            }

        return None

    def _create_task_event(
        self,
        task_id: str,
        context_id: str,
        result: Any,
        original_message: Message | None,
    ) -> Task:
        """Create an A2A Task event from execution result.

        Args:
            task_id: Task identifier
            context_id: Context identifier
            result: Result from Keycard executor
            original_message: Original message from request

        Returns:
            A2A Task event with completed status
        """
        # Convert result to string
        result_str = str(result)

        # Create response message
        response_message = Message(
            message_id=f"msg-{uuid.uuid4().hex[:8]}",
            role="agent",
            parts=[{"text": result_str}],
        )

        # Create task with completed status
        task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(
                state=TaskState.completed,
            ),
            history=[response_message],
        )

        # Add original message to history if available
        if original_message:
            task.history.insert(0, original_message)

        return task

    def _create_failed_task_event(
        self,
        task_id: str,
        context_id: str,
        error: str,
        original_message: Message | None,
    ) -> Task:
        """Create an A2A Task event for failed execution.

        Args:
            task_id: Task identifier
            context_id: Context identifier
            error: Error message
            original_message: Original message from request

        Returns:
            A2A Task event with failed status
        """
        # Create error message
        error_message = Message(
            message_id=f"msg-{uuid.uuid4().hex[:8]}",
            role="agent",
            parts=[{"text": f"Error: {error}"}],
        )

        # Create task with failed status
        task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(
                state=TaskState.failed,
                message=error_message,
            ),
            history=[error_message],
        )

        # Add original message to history if available
        if original_message:
            task.history.insert(0, original_message)

        return task
