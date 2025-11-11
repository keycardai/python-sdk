"""Unit tests for the Connection base class.

This module tests the Connection base class, focusing on:
- Initialization and internal state management
- Connection lifecycle (start, stop, connect_task)
- Error handling during connect and disconnect
- Abstract method enforcement
- Task management and cleanup

Note: Specific connection implementations (HTTP, stdio, etc.) are tested separately.
"""

import asyncio
from typing import Any
from unittest.mock import Mock

import pytest

from keycardai.mcp.client.connection.base import Connection


# Concrete implementation for testing the abstract base class
class ConcreteConnection(Connection):
    """Minimal concrete implementation for testing abstract base."""

    def __init__(self):
        super().__init__()
        self.connect_called = False
        self.connect_call_count = 0
        self.disconnect_called = False
        self.disconnect_call_count = 0
        self.should_raise_on_connect: Exception | None = None
        self.should_raise_on_disconnect: Exception | None = None
        self.mock_read_stream = Mock()
        self.mock_write_stream = Mock()
        self.connect_delay = 0.0  # Delay for testing timing

    async def connect(self) -> tuple[Any, Any]:
        """Track connect calls and return mock streams."""
        self.connect_called = True
        self.connect_call_count += 1

        if self.connect_delay > 0:
            await asyncio.sleep(self.connect_delay)

        if self.should_raise_on_connect:
            raise self.should_raise_on_connect

        return self.mock_read_stream, self.mock_write_stream

    async def disconnect(self) -> None:
        """Track disconnect calls."""
        self.disconnect_called = True
        self.disconnect_call_count += 1

        if self.should_raise_on_disconnect:
            raise self.should_raise_on_disconnect


class TestConnectionInitialization:
    """Test Connection initialization and internal state."""

    def test_default_initialization(self):
        """Test that connection initializes with correct default state."""
        connection = ConcreteConnection()

        assert connection._ready is not None
        assert isinstance(connection._ready, asyncio.Event)
        assert not connection._ready.is_set()

        assert connection._done is not None
        assert isinstance(connection._done, asyncio.Event)
        assert not connection._done.is_set()

        assert connection._stop is not None
        assert isinstance(connection._stop, asyncio.Event)
        assert not connection._stop.is_set()

        assert connection._exception is None
        assert connection._task is None
        assert connection._connection is None


class TestConnectionStart:
    """Test Connection.start() method and successful connection lifecycle."""

    @pytest.mark.asyncio
    async def test_start_calls_connect(self):
        """Test that start() calls the connect() method."""
        connection = ConcreteConnection()
        try:
            await connection.start()
            assert connection.connect_called
            assert connection.connect_call_count == 1
        finally:
            await connection.stop()

    @pytest.mark.asyncio
    async def test_start_returns_streams(self):
        """Test that start() returns the read and write streams."""
        connection = ConcreteConnection()
        try:
            read_stream, write_stream = await connection.start()
            assert read_stream is connection.mock_read_stream
            assert write_stream is connection.mock_write_stream
        finally:
            await connection.stop()

    @pytest.mark.asyncio
    async def test_start_creates_background_task(self):
        """Test that start() creates a background task."""
        connection = ConcreteConnection()
        try:
            await connection.start()
            assert connection._task is not None
            assert isinstance(connection._task, asyncio.Task)
            assert not connection._task.done()
        finally:
            await connection.stop()

    @pytest.mark.asyncio
    async def test_start_clears_events(self):
        """Test that start() clears all events before beginning."""
        connection = ConcreteConnection()
        try:
            await connection.start()

            # Events should have been cleared during start
            # After start completes, _ready should be set but others cleared
            assert connection._ready.is_set()
            assert not connection._stop.is_set()
        finally:
            await connection.stop()

    @pytest.mark.asyncio
    async def test_start_sets_ready_event(self):
        """Test that start() sets the ready event after connection."""
        connection = ConcreteConnection()
        try:
            await connection.start()
            assert connection._ready.is_set()
        finally:
            await connection.stop()

    @pytest.mark.asyncio
    async def test_start_stores_connection_tuple(self):
        """Test that start() stores the connection tuple internally."""
        connection = ConcreteConnection()
        try:
            await connection.start()
            assert connection._connection is not None
            assert connection._connection == (connection.mock_read_stream, connection.mock_write_stream)
        finally:
            await connection.stop()

    @pytest.mark.asyncio
    async def test_start_task_name_includes_class_name(self):
        """Test that the background task name includes the class name."""
        connection = ConcreteConnection()
        try:
            await connection.start()
            assert connection._task is not None
            assert "ConcreteConnection_connection" in connection._task.get_name()
        finally:
            await connection.stop()


class TestConnectionStop:
    """Test Connection.stop() method and connection cleanup."""

    @pytest.mark.asyncio
    async def test_stop_sets_stop_event(self):
        """Test that stop() sets the stop event."""
        connection = ConcreteConnection()
        await connection.start()
        await connection.stop()
        assert connection._stop.is_set()

    @pytest.mark.asyncio
    async def test_stop_waits_for_task_completion(self):
        """Test that stop() waits for the background task to complete."""
        connection = ConcreteConnection()
        await connection.start()
        await connection.stop()
        assert connection._task is not None
        assert connection._task.done()

    @pytest.mark.asyncio
    async def test_stop_calls_disconnect(self):
        """Test that stop() triggers disconnect() call."""
        connection = ConcreteConnection()
        await connection.start()
        await connection.stop()
        assert connection.disconnect_called
        assert connection.disconnect_call_count == 1

    @pytest.mark.asyncio
    async def test_stop_sets_done_event(self):
        """Test that stop() sets the done event."""
        connection = ConcreteConnection()
        await connection.start()
        await connection.stop()
        assert connection._done.is_set()

    @pytest.mark.asyncio
    async def test_stop_clears_connection_tuple(self):
        """Test that stop() clears the connection tuple."""
        connection = ConcreteConnection()
        await connection.start()
        assert connection._connection is not None
        await connection.stop()
        assert connection._connection is None

    @pytest.mark.asyncio
    async def test_stop_when_task_already_done(self):
        """Test that stop() handles case when task is already done."""
        connection = ConcreteConnection()
        await connection.start()
        connection._stop.set()
        await connection._task
        await connection.stop()
        assert connection._done.is_set()

