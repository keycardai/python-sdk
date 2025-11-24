"""
Base connection abstraction for MCP client.

Provides the Connection base class that all transport implementations extend.
"""
import asyncio
from typing import Any


class ConnectionError(Exception):
    """Raised when a connection cannot be established."""

    def __init__(self, message: str | None = None):
        super().__init__(message or "failed to establish connection")


class Connection:
    """
    Base class for MCP server connections.

    Manages the lifecycle of a connection:
    - start(): Begin connection (async, returns read/write streams)
    - stop(): Terminate connection
    - connect(): Implementation-specific connection logic (abstract)
    - disconnect(): Implementation-specific cleanup (optional)
    """

    def __init__(self):
        self._ready = asyncio.Event()
        self._done = asyncio.Event()
        self._stop = asyncio.Event()
        self._exception: Exception | None = None
        self._task: asyncio.Task | None = None
        self._connection: tuple[Any, Any] | None = None

    async def start(self) -> tuple[Any, Any]:
        """
        Start the connection.

        Returns:
            Tuple of (read_stream, write_stream)

        Raises:
            ConnectionError: If connection fails to establish
            Exception: Any exception raised during connection establishment
        """
        self._ready.clear()
        self._done.clear()
        self._stop.clear()

        self._task = asyncio.create_task(self.connect_task(), name=f"{self.__class__.__name__}_connection")

        await self._ready.wait()

        if self._exception:
            raise self._exception

        if self._connection is None:
            raise ConnectionError()
        return self._connection

    async def stop(self) -> None:
        """Stop the connection and clean up resources."""
        if self._task and not self._task.done():
            self._stop.set()
            try:
                await self._task
            except asyncio.CancelledError:
                # Task was cancelled - this is expected during cleanup
                pass

        # Wait for cleanup to complete if not already done
        if not self._done.is_set():
            try:
                await self._done.wait()
            except asyncio.CancelledError:
                # Cleanup was cancelled - acceptable during teardown
                pass

    async def connect_task(self) -> None:
        """
        Background task that manages connection lifecycle.

        This task:
        1. Calls connect() to establish connection
        2. Signals ready when connection is established
        3. Waits for stop signal
        4. Calls disconnect() on cleanup
        """
        try:
            self._connection = await self.connect()

            self._ready.set()
            await self._stop.wait()
        except Exception as e:
            self._exception = e
            self._ready.set()
            raise e
        finally:
            if self._connection is not None:
                try:
                    await self.disconnect()
                except Exception as e:
                    self._exception = e
                self._connection = None
            self._done.set()

    async def connect(self) -> tuple[Any, Any]:
        """
        Establish the connection and return (read_stream, write_stream).
        Must be implemented by subclasses.

        Returns:
            Tuple of (read_stream, write_stream)

        Raises:
            NotImplementedError: If not implemented by subclass
        """
        raise NotImplementedError()

    async def disconnect(self) -> None:
        """
        Clean up connection resources.
        Optional - override in subclasses if cleanup is needed.
        """
        pass

