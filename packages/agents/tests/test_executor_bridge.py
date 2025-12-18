"""Tests for KeycardToA2AExecutorBridge.

This module tests the bridge that allows Keycard's simple synchronous executor
interface to work with A2A's event-driven asynchronous JSONRPC protocol.
"""

import pytest
from unittest.mock import AsyncMock, Mock

from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Message,
    MessageSendParams,
    Role,
    Task,
    TaskState,
)

from keycardai.agents.server.executor import SimpleExecutor, LambdaExecutor
from keycardai.agents.server.executor_bridge import KeycardToA2AExecutorBridge


@pytest.fixture
def simple_executor():
    """Create a simple executor for testing."""
    return SimpleExecutor()


@pytest.fixture
def lambda_executor():
    """Create a lambda executor that returns a specific result."""
    def my_func(task, inputs):
        return f"Processed: {task}"
    return LambdaExecutor(my_func)


@pytest.fixture
def mock_event_queue():
    """Create a mock event queue."""
    queue = AsyncMock(spec=EventQueue)
    return queue


@pytest.fixture
def simple_request_context():
    """Create a simple RequestContext with text message."""
    message = Message(
        message_id="msg-123",
        role=Role.user,
        parts=[{"text": "Hello, agent!"}],
    )
    params = MessageSendParams(message=message)
    context = RequestContext(
        request=params,
        task_id="task-456",
        context_id="ctx-789",
    )
    return context


@pytest.fixture
def context_with_metadata():
    """Create RequestContext with metadata (for inputs)."""
    message = Message(
        message_id="msg-123",
        role=Role.user,
        parts=[{"text": "Process data"}],
    )
    params = MessageSendParams(
        message=message,
        metadata={"key1": "value1", "key2": "value2", "_internal": "skip"},
    )
    context = RequestContext(
        request=params,
        task_id="task-456",
        context_id="ctx-789",
    )
    return context


@pytest.fixture
def context_with_delegation_token():
    """Create RequestContext with delegation token in metadata."""
    message = Message(
        message_id="msg-123",
        role=Role.user,
        parts=[{"text": "Delegated task"}],
    )
    params = MessageSendParams(
        message=message,
        metadata={"access_token": "delegation_token_123"},
    )
    context = RequestContext(
        request=params,
        task_id="task-456",
        context_id="ctx-789",
    )
    return context


class TestBridgeInitialization:
    """Test bridge initialization."""

    def test_init_with_simple_executor(self, simple_executor):
        """Test initializing bridge with SimpleExecutor."""
        bridge = KeycardToA2AExecutorBridge(simple_executor)
        assert bridge.keycard_executor == simple_executor

    def test_init_with_lambda_executor(self, lambda_executor):
        """Test initializing bridge with LambdaExecutor."""
        bridge = KeycardToA2AExecutorBridge(lambda_executor)
        assert bridge.keycard_executor == lambda_executor


class TestTaskExtraction:
    """Test extracting task from A2A RequestContext."""

    def test_extract_task_from_text_part(self, simple_executor):
        """Test extracting task description from text parts."""
        bridge = KeycardToA2AExecutorBridge(simple_executor)

        message = Message(
            message_id="msg-123",
            role=Role.user,
            parts=[{"text": "Analyze this PR"}],
        )
        params = MessageSendParams(message=message)
        context = RequestContext(request=params)

        task = bridge._extract_task_from_context(context)
        assert task == "Analyze this PR"

    def test_extract_task_from_multiple_parts(self, simple_executor):
        """Test extracting task from multiple text parts."""
        bridge = KeycardToA2AExecutorBridge(simple_executor)

        message = Message(
            message_id="msg-123",
            role=Role.user,
            parts=[
                {"text": "First part"},
                {"text": "Second part"},
            ],
        )
        params = MessageSendParams(message=message)
        context = RequestContext(request=params)

        task = bridge._extract_task_from_context(context)
        assert "First part" in task
        assert "Second part" in task

    def test_extract_task_no_message(self, simple_executor):
        """Test handling missing message."""
        bridge = KeycardToA2AExecutorBridge(simple_executor)
        context = RequestContext(request=None)

        task = bridge._extract_task_from_context(context)
        assert task == "No task description provided"


class TestInputsExtraction:
    """Test extracting inputs from A2A RequestContext metadata."""

    def test_extract_inputs_from_metadata(self, simple_executor, context_with_metadata):
        """Test extracting inputs from metadata."""
        bridge = KeycardToA2AExecutorBridge(simple_executor)

        inputs = bridge._extract_inputs_from_context(context_with_metadata)

        assert inputs is not None
        assert inputs["key1"] == "value1"
        assert inputs["key2"] == "value2"
        # Internal fields (starting with _) should be excluded
        assert "_internal" not in inputs

    def test_extract_inputs_no_metadata(self, simple_executor, simple_request_context):
        """Test handling missing metadata."""
        bridge = KeycardToA2AExecutorBridge(simple_executor)

        inputs = bridge._extract_inputs_from_context(simple_request_context)
        # Should return None or empty dict when no metadata
        assert inputs is None or inputs == {}


class TestExecutorExecution:
    """Test bridge execution flow."""

    @pytest.mark.asyncio
    async def test_execute_simple_task(
        self, lambda_executor, simple_request_context, mock_event_queue
    ):
        """Test executing a simple task through the bridge."""
        bridge = KeycardToA2AExecutorBridge(lambda_executor)

        await bridge.execute(simple_request_context, mock_event_queue)

        # Verify event was enqueued
        assert mock_event_queue.enqueue_event.called
        call_args = mock_event_queue.enqueue_event.call_args
        task_event = call_args[0][0]

        # Verify it's a Task with completed status
        assert isinstance(task_event, Task)
        assert task_event.status.state == TaskState.completed
        assert task_event.id == "task-456"
        assert task_event.context_id == "ctx-789"

        # Verify result is in history
        assert len(task_event.history) > 0
        response_message = task_event.history[-1]
        assert response_message.role == Role.agent
        assert "Processed: Hello, agent!" in str(response_message.parts)

    @pytest.mark.asyncio
    async def test_execute_with_inputs(
        self, lambda_executor, context_with_metadata, mock_event_queue
    ):
        """Test executing task with inputs from metadata."""
        bridge = KeycardToA2AExecutorBridge(lambda_executor)

        await bridge.execute(context_with_metadata, mock_event_queue)

        # Verify execution completed
        assert mock_event_queue.enqueue_event.called

    @pytest.mark.asyncio
    async def test_execute_with_delegation_token(
        self, context_with_delegation_token, mock_event_queue
    ):
        """Test that delegation token is passed to executor."""
        # Create an executor with delegation support
        mock_executor = Mock()
        mock_executor.execute.return_value = "Result with delegation"
        mock_executor.set_token_for_delegation = Mock()

        bridge = KeycardToA2AExecutorBridge(mock_executor)

        await bridge.execute(context_with_delegation_token, mock_event_queue)

        # Verify token was set
        mock_executor.set_token_for_delegation.assert_called_once_with(
            "delegation_token_123"
        )

        # Verify execution happened
        assert mock_executor.execute.called


class TestErrorHandling:
    """Test bridge error handling."""

    @pytest.mark.asyncio
    async def test_execute_with_exception(
        self, simple_request_context, mock_event_queue
    ):
        """Test handling executor exceptions."""
        # Create executor that raises exception
        failing_executor = Mock()
        failing_executor.execute.side_effect = RuntimeError("Execution failed!")

        bridge = KeycardToA2AExecutorBridge(failing_executor)

        await bridge.execute(simple_request_context, mock_event_queue)

        # Verify failed task event was enqueued
        assert mock_event_queue.enqueue_event.called
        call_args = mock_event_queue.enqueue_event.call_args
        task_event = call_args[0][0]

        # Verify it's a failed Task
        assert isinstance(task_event, Task)
        assert task_event.status.state == TaskState.failed
        assert task_event.id == "task-456"

        # Verify error message is in history or status
        if task_event.status.message:
            assert "Error: Execution failed!" in str(task_event.status.message.parts)
        elif task_event.history:
            error_msg = task_event.history[-1]
            assert "Error: Execution failed!" in str(error_msg.parts)


class TestTaskCancellation:
    """Test bridge cancellation handling."""

    @pytest.mark.asyncio
    async def test_cancel_task(
        self, simple_executor, simple_request_context, mock_event_queue
    ):
        """Test task cancellation."""
        bridge = KeycardToA2AExecutorBridge(simple_executor)

        await bridge.cancel(simple_request_context, mock_event_queue)

        # Verify canceled task event was enqueued
        assert mock_event_queue.enqueue_event.called
        call_args = mock_event_queue.enqueue_event.call_args
        task_event = call_args[0][0]

        # Verify it's a canceled Task
        assert isinstance(task_event, Task)
        assert task_event.status.state == TaskState.canceled
        assert task_event.id == "task-456"


class TestTaskEventCreation:
    """Test A2A Task event creation from results."""

    def test_create_task_event_with_string_result(self, simple_executor):
        """Test creating task event from string result."""
        bridge = KeycardToA2AExecutorBridge(simple_executor)

        message = Message(
            message_id="msg-123",
            role=Role.user,
            parts=[{"text": "Original request"}],
        )

        task = bridge._create_task_event(
            task_id="task-456",
            context_id="ctx-789",
            result="Task completed successfully",
            original_message=message,
        )

        assert task.id == "task-456"
        assert task.context_id == "ctx-789"
        assert task.status.state == TaskState.completed
        assert len(task.history) == 2  # Original + response
        assert task.history[0].message_id == "msg-123"
        assert task.history[1].role == Role.agent

    def test_create_failed_task_event(self, simple_executor):
        """Test creating failed task event."""
        bridge = KeycardToA2AExecutorBridge(simple_executor)

        message = Message(
            message_id="msg-123",
            role=Role.user,
            parts=[{"text": "Original request"}],
        )

        task = bridge._create_failed_task_event(
            task_id="task-456",
            context_id="ctx-789",
            error="Something went wrong",
            original_message=message,
        )

        assert task.id == "task-456"
        assert task.status.state == TaskState.failed
        assert len(task.history) == 2
        assert "Error: Something went wrong" in str(task.history[1].parts)
