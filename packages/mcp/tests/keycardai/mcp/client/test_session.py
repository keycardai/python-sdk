"""Unit tests for the Session class.

This module tests the Session class initialization, connection lifecycle,
disconnection, tool calling, authentication challenge handling, and status
lifecycle transitions.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from mcp.types import ListToolsResult, PaginatedRequestParams, Tool

from keycardai.mcp.client.auth.coordinators import LocalAuthCoordinator
from keycardai.mcp.client.auth.coordinators.base import AuthCoordinator
from keycardai.mcp.client.connection.base import Connection
from keycardai.mcp.client.session import Session, SessionStatus, SessionStatusCategory
from keycardai.mcp.client.storage import InMemoryBackend


# Mock AuthCoordinator for testing
class MockAuthCoordinator(AuthCoordinator):
    """Mock coordinator that tracks method calls."""

    def __init__(self, storage=None):
        super().__init__(storage)
        self.start_called = False
        self.shutdown_called = False

    @property
    def endpoint_type(self) -> str:
        """Return test endpoint type."""
        return "test"

    async def get_callback_uris(self) -> list[str] | None:
        """Return mock callback URIs."""
        return ["http://localhost:8080/callback"]

    async def start(self):
        self.start_called = True

    async def shutdown(self):
        self.shutdown_called = True

    async def handle_redirect(self, authorization_url: str, metadata: dict):
        pass


# Mock Connection for testing
class MockConnection(Connection):
    """Mock connection that tracks method calls."""

    def __init__(self):
        self.start_called = False
        self.start_call_count = 0
        self.stop_called = False
        self.stop_call_count = 0
        self.should_raise_on_start = None
        self.read_stream = Mock()
        self.write_stream = Mock()

    async def start(self):
        """Track start calls and return mock streams."""
        self.start_called = True
        self.start_call_count += 1

        if self.should_raise_on_start:
            raise self.should_raise_on_start

        return self.read_stream, self.write_stream

    async def stop(self):
        """Track stop calls."""
        self.stop_called = True
        self.stop_call_count += 1


# Mock ClientSession for testing
class MockClientSession:
    """Mock MCP ClientSession."""

    def __init__(self):
        self.initialize_called = False
        self.initialize_call_count = 0
        self.should_raise_on_initialize = None
        self.call_tool_calls = []
        self.list_tools_calls = []
        self.list_tools_responses = []  # List of ListToolsResult to return
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exited = True

    async def initialize(self):
        """Track initialize calls."""
        self.initialize_called = True
        self.initialize_call_count += 1

        if self.should_raise_on_initialize:
            raise self.should_raise_on_initialize

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]):
        """Track tool calls."""
        self.call_tool_calls.append((tool_name, arguments))
        return {"result": f"called {tool_name}", "arguments": arguments}

    async def list_tools(self, params: PaginatedRequestParams | None = None):
        """Track list_tools calls and return paginated responses."""
        self.list_tools_calls.append(params)

        # Return responses in sequence based on call count
        if self.list_tools_responses:
            call_index = len(self.list_tools_calls) - 1
            if call_index < len(self.list_tools_responses):
                return self.list_tools_responses[call_index]

        # Default: return empty list with no next cursor
        return ListToolsResult(tools=[], nextCursor=None)

    async def send_ping(self):
        """Mock ping for health checks."""
        # Return empty result (MCP ping returns EmptyResult)
        return {}


class TestSessionInitialization:
    """Test Session initialization with various configurations."""

    def test_default_initialization(self):
        """Test that a session is created with minimal config."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        assert session.server_name == "test_server"
        assert session.server_config == server_config
        assert session.context is context
        assert session.coordinator is coordinator
        assert session._session is None
        assert session._connection is None
        assert session._connected is False

    def test_initialization_creates_server_storage_namespace(self):
        """Test that session creates a server-specific storage namespace."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        assert session.server_storage is not None
        assert session.server_storage is not context.storage

    def test_initialization_with_different_server_names(self):
        """Test session initialization with various server names."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        test_names = ["slack", "github", "my-server", "server_123"]

        for server_name in test_names:
            server_config = {"url": "http://localhost:3000"}
            session = Session(server_name, server_config, context, coordinator)

            assert session.server_name == server_name

    def test_initialization_no_side_effects(self):
        """Test that creating a session has no side effects."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        # Should not be connected
        assert session._connected is False
        assert session._connection is None
        assert session._session is None

        # Coordinator should not be started
        assert coordinator.start_called is False


class TestSessionConnect:
    """Test Session connect method with various scenarios."""

    @pytest.mark.asyncio
    async def test_connect_creates_connection_and_session(self):
        """Test that connect creates connection and initializes session."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

        assert session._connected is True
        assert session._connection is mock_connection
        assert session._session is mock_client_session
        assert mock_connection.start_called is True
        assert mock_client_session.entered is True
        assert mock_client_session.initialize_called is True

    @pytest.mark.asyncio
    async def test_connect_when_already_connected_returns_early(self):
        """Test that connect returns early when already connected."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

                # Reset call tracking
                initial_start_count = mock_connection.start_call_count
                initial_initialize_count = mock_client_session.initialize_call_count

                # Connect again
                await session.connect()

                # Should not have called start or initialize again
                assert mock_connection.start_call_count == initial_start_count
                assert mock_client_session.initialize_call_count == initial_initialize_count

    @pytest.mark.asyncio
    async def test_connect_reuses_existing_connection(self):
        """Test that connect reuses existing connection if available."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        session._connection = mock_connection

        mock_client_session = MockClientSession()

        with patch("keycardai.mcp.client.session.create_connection") as create_mock:
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

        # Should not create new connection
        create_mock.assert_not_called()
        # Should use existing connection
        assert mock_connection.start_called is True

    @pytest.mark.asyncio
    async def test_connect_passes_correct_parameters_to_create_connection(self):
        """Test that connect passes correct parameters when creating connection."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection) as create_mock:
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

        create_mock.assert_called_once_with(
            server_name="test_server",
            server_config=server_config,
            context=context,
            coordinator=coordinator,
            server_storage=session.server_storage  # Now expects server-scoped storage
        )

    @pytest.mark.asyncio
    async def test_connect_handles_auth_challenge_gracefully(self):
        """Test that connect handles auth challenge without raising."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        # Set up auth challenge via coordinator
        await coordinator.set_auth_pending(
            context_id=context.id,
            server_name="test_server",
            auth_metadata={
                "authorization_url": "http://auth.example.com",
                "state": "state123"
            }
        )

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()
        mock_client_session.should_raise_on_initialize = RuntimeError("Auth required")

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                # Should not raise
                await session.connect()

        # Should have disconnected
        assert session._connected is False
        assert mock_connection.stop_called is True

    @pytest.mark.asyncio
    async def test_connect_retries_on_connection_closed_after_auth(self):
        """Test that connect retries once when connection closes after auth."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection1 = MockConnection()
        mock_connection2 = MockConnection()
        mock_client_session1 = MockClientSession()
        mock_client_session1.should_raise_on_initialize = RuntimeError("Connection closed")
        mock_client_session2 = MockClientSession()

        connection_calls = [mock_connection1, mock_connection2]
        session_calls = [mock_client_session1, mock_client_session2]

        with patch("keycardai.mcp.client.session.create_connection", side_effect=connection_calls):
            with patch("keycardai.mcp.client.session.ClientSession", side_effect=session_calls):
                await session.connect()

        # Should have retried and succeeded
        assert session._connected is True
        assert mock_connection2.start_called is True
        assert mock_client_session2.initialize_called is True

    @pytest.mark.asyncio
    async def test_connect_does_not_retry_twice(self):
        """Test that connect does not retry more than once."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()
        # Fail with "Connection closed" message
        mock_client_session.should_raise_on_initialize = RuntimeError("Connection closed")

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                # Should not raise - sets failure status instead
                await session.connect()

                # Should have failed after retry
                assert session.status == SessionStatus.CONNECTION_FAILED
                assert not session.is_operational

    @pytest.mark.asyncio
    async def test_connect_handles_initialization_error(self):
        """Test that connect handles initialization errors gracefully."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()
        mock_client_session.should_raise_on_initialize = ValueError("Invalid config")

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                # Should not raise - sets failure status instead
                await session.connect()

        # Should have cleaned up and set failure status
        assert session._connected is False
        assert session.status == SessionStatus.FAILED
        assert session.is_failed

    @pytest.mark.asyncio
    async def test_connect_cleans_up_on_error(self):
        """Test that connect cleans up resources on error."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()
        mock_client_session.should_raise_on_initialize = RuntimeError("Initialization failed")

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                # Should not raise - sets failure status instead
                await session.connect()

        # Should have cleaned up and set failure status
        assert session._connected is False
        assert mock_connection.stop_called is True
        assert mock_client_session.exited is True
        assert session.status == SessionStatus.FAILED
        assert session.is_failed

    @pytest.mark.asyncio
    async def test_connect_disconnects_existing_session_if_connected_but_no_session(self):
        """Test that connect disconnects if marked connected but no session."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        # Simulate edge case: connected flag is True but no session
        session._connected = True
        session._session = None

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

        # Should have reconnected successfully
        assert session._connected is True
        assert session._session is mock_client_session


class TestSessionDisconnect:
    """Test Session disconnect method."""

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected_is_safe(self):
        """Test that disconnect when not connected is a no-op."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        # Should not raise
        await session.disconnect()

        assert session._connected is False

    @pytest.mark.asyncio
    async def test_disconnect_closes_session_and_connection(self):
        """Test that disconnect properly closes session and connection."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

        assert session._connected is True

        await session.disconnect()

        assert session._connected is False
        assert mock_client_session.exited is True
        assert mock_connection.stop_called is True
        assert session._session is None
        assert session._connection is None

    @pytest.mark.asyncio
    async def test_disconnect_handles_session_exit_error(self):
        """Test that disconnect handles errors during session exit."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()

        # Create a session that raises on exit
        class FailingClientSession(MockClientSession):
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                await super().__aexit__(exc_type, exc_val, exc_tb)
                raise RuntimeError("Exit failed")

        mock_client_session = FailingClientSession()

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

        # Should not raise, but still clean up
        await session.disconnect()

        assert session._connected is False
        assert session._session is None
        # Connection stop should still be called
        assert mock_connection.stop_called is True

    @pytest.mark.asyncio
    async def test_disconnect_handles_connection_stop_error(self):
        """Test that disconnect handles errors during connection stop."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        # Create a connection that raises on stop
        class FailingConnection(MockConnection):
            async def stop(self):
                await super().stop()
                raise RuntimeError("Stop failed")

        mock_connection = FailingConnection()
        mock_client_session = MockClientSession()

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

        # Should not raise, but still clean up
        await session.disconnect()

        assert session._connected is False
        assert session._session is None
        assert session._connection is None

    @pytest.mark.asyncio
    async def test_disconnect_sets_connected_to_false_first(self):
        """Test that disconnect sets connected flag to False immediately."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

        assert session._connected is True

        await session.disconnect()

        # Should be False immediately
        assert session._connected is False

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up_even_with_both_errors(self):
        """Test that disconnect cleans up even when both session and connection fail."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        # Create failing implementations
        class FailingClientSession(MockClientSession):
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                await super().__aexit__(exc_type, exc_val, exc_tb)
                raise RuntimeError("Session exit failed")

        class FailingConnection(MockConnection):
            async def stop(self):
                await super().stop()
                raise RuntimeError("Connection stop failed")

        mock_connection = FailingConnection()
        mock_client_session = FailingClientSession()

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

        # Should not raise, and should clean up
        await session.disconnect()

        assert session._connected is False
        assert session._session is None
        assert session._connection is None


class TestSessionCallTool:
    """Test Session call_tool method."""

    @pytest.mark.asyncio
    async def test_call_tool_delegates_to_client_session(self):
        """Test that call_tool delegates to the underlying ClientSession."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

        result = await session.call_tool("test_tool", {"arg1": "value1", "arg2": 42})

        assert len(mock_client_session.call_tool_calls) == 1
        assert mock_client_session.call_tool_calls[0] == ("test_tool", {"arg1": "value1", "arg2": 42})
        assert result["result"] == "called test_tool"
        assert result["arguments"] == {"arg1": "value1", "arg2": 42}

    @pytest.mark.asyncio
    async def test_call_tool_with_empty_arguments(self):
        """Test that call_tool works with empty arguments."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

        await session.call_tool("simple_tool", {})

        assert len(mock_client_session.call_tool_calls) == 1
        assert mock_client_session.call_tool_calls[0] == ("simple_tool", {})

    @pytest.mark.asyncio
    async def test_call_tool_multiple_times(self):
        """Test that call_tool can be called multiple times."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

        await session.call_tool("tool1", {"arg": "value1"})
        await session.call_tool("tool2", {"arg": "value2"})
        await session.call_tool("tool3", {"arg": "value3"})

        assert len(mock_client_session.call_tool_calls) == 3
        assert mock_client_session.call_tool_calls[0] == ("tool1", {"arg": "value1"})
        assert mock_client_session.call_tool_calls[1] == ("tool2", {"arg": "value2"})
        assert mock_client_session.call_tool_calls[2] == ("tool3", {"arg": "value3"})


class TestSessionListTools:
    """Test Session list_tools delegation to upstream ClientSession."""

    @pytest.mark.asyncio
    async def test_list_tools_delegates_to_client_session(self):
        """Test that list_tools delegates to underlying ClientSession."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()

        # Setup single page response
        mock_client_session.list_tools_responses = [
            ListToolsResult(
                tools=[
                    Tool(name="tool1", description="First tool", inputSchema={"type": "object"}),
                    Tool(name="tool2", description="Second tool", inputSchema={"type": "object"}),
                ],
                nextCursor=None  # No more pages
            )
        ]

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                await session.connect()

        result = await session.list_tools()

        # Should delegate to underlying ClientSession
        assert len(mock_client_session.list_tools_calls) == 1
        assert mock_client_session.list_tools_calls[0] is None

        # Should return ListToolsResult (not processed list)
        assert isinstance(result, ListToolsResult)
        assert len(result.tools) == 2
        assert result.tools[0].name == "tool1"
        assert result.tools[1].name == "tool2"


class TestSessionRequiresAuth:
    """Test Session requires_auth method."""

    @pytest.mark.asyncio
    async def test_requires_auth_returns_true_when_challenge_exists(self):
        """Test that requires_auth returns True when auth challenge exists."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        # Set up auth challenge via coordinator
        await coordinator.set_auth_pending(
            context_id=context.id,
            server_name="test_server",
            auth_metadata={
                "authorization_url": "http://auth.example.com",
                "state": "state123"
            }
        )

        result = await session.requires_auth()

        assert result is True

    @pytest.mark.asyncio
    async def test_requires_auth_returns_false_when_no_challenge(self):
        """Test that requires_auth returns False when no auth challenge."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        result = await session.requires_auth()

        assert result is False


class TestSessionGetAuthChallenge:
    """Test Session get_auth_challenge method."""

    @pytest.mark.asyncio
    async def test_get_auth_challenge_returns_challenge_when_exists(self):
        """Test that get_auth_challenge returns the challenge details."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        # Set up auth challenge via coordinator
        challenge = {
            "authorization_url": "http://auth.example.com",
            "state": "state123"
        }
        await coordinator.set_auth_pending(
            context_id=context.id,
            server_name="test_server",
            auth_metadata=challenge
        )

        result = await session.get_auth_challenge()

        assert result == challenge
        assert result["authorization_url"] == "http://auth.example.com"
        assert result["state"] == "state123"

    @pytest.mark.asyncio
    async def test_get_auth_challenge_returns_none_when_no_challenge(self):
        """Test that get_auth_challenge returns None when no challenge exists."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        result = await session.get_auth_challenge()

        assert result is None

    @pytest.mark.asyncio
    async def test_get_auth_challenge_with_different_strategies(self):
        """Test that get_auth_challenge works with different strategy types."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        # OAuth challenge
        oauth_challenge = {
            "authorization_url": "http://oauth.example.com",
            "state": "oauth_state_123"
        }
        await coordinator.set_auth_pending(
            context_id=context.id,
            server_name="test_server",
            auth_metadata=oauth_challenge
        )
        result = await session.get_auth_challenge()
        assert result == oauth_challenge

        # Custom challenge format
        custom_challenge = {
            "challenge_type": "custom",
            "url": "http://custom.example.com",
            "metadata": {"key": "value"}
        }
        await coordinator.set_auth_pending(
            context_id=context.id,
            server_name="test_server",
            auth_metadata=custom_challenge
        )
        result = await session.get_auth_challenge()
        assert result == custom_challenge


class TestSessionStorageIsolation:
    """Test Session storage isolation between different sessions."""

    @pytest.mark.asyncio
    async def test_server_storage_is_isolated_between_sessions(self):
        """Test that different sessions have isolated server storage."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session1 = Session("server1", server_config, context, coordinator)
        session2 = Session("server2", server_config, context, coordinator)

        # Write to session1's storage
        await session1.server_storage.set("token", "session1_token")

        # Session2's storage should not have this value
        value = await session2.server_storage.get("token")
        assert value is None

        # Write to session2's storage
        await session2.server_storage.set("token", "session2_token")

        # Verify isolation
        value1 = await session1.server_storage.get("token")
        value2 = await session2.server_storage.get("token")

        assert value1 == "session1_token"
        assert value2 == "session2_token"

    @pytest.mark.asyncio
    async def test_server_storage_namespace_format(self):
        """Test that server storage uses correct namespace format."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        # Storage should be namespaced
        assert session.server_storage is not None
        assert session.server_storage is not context.storage

        # Write to server storage and verify it's isolated
        await session.server_storage.set("key", "value")

        # Direct access to context storage should not have this key
        direct_value = await context.storage.get("key")
        assert direct_value is None


class TestSessionEdgeCases:
    """Test Session edge cases and error scenarios."""

    @pytest.mark.asyncio
    async def test_connect_with_internal_retry_flag(self):
        """Test that the internal _retry_after_auth flag works correctly."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        mock_connection = MockConnection()
        mock_client_session = MockClientSession()
        mock_client_session.should_raise_on_initialize = RuntimeError("Connection closed")

        with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
            with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                # Call with _retry_after_auth=False should not retry
                await session.connect(_retry_after_auth=False)

        # Should have failed and cleaned up, with failure status set
        assert session._connected is False
        assert session.status == SessionStatus.CONNECTION_FAILED
        assert session.is_failed

    @pytest.mark.asyncio
    async def test_session_with_complex_server_config(self):
        """Test session initialization with complex server configuration."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {
            "url": "http://localhost:3000",
            "transport": "http",
            "auth": {
                "type": "oauth",
                "client_id": "abc123"
            },
            "extra_field": "value"
        }
        session = Session("test_server", server_config, context, coordinator)

        assert session.server_config == server_config
        assert session.server_config["url"] == "http://localhost:3000"
        assert session.server_config["auth"]["client_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_multiple_connect_disconnect_cycles(self):
        """Test that session can handle multiple connect/disconnect cycles."""
        storage = InMemoryBackend()
        coordinator = MockAuthCoordinator(storage)
        context = coordinator.create_context("user:alice")

        server_config = {"url": "http://localhost:3000"}
        session = Session("test_server", server_config, context, coordinator)

        for _i in range(3):
            mock_connection = MockConnection()
            mock_client_session = MockClientSession()

            with patch("keycardai.mcp.client.session.create_connection", return_value=mock_connection):
                with patch("keycardai.mcp.client.session.ClientSession", return_value=mock_client_session):
                    await session.connect()

            assert session._connected is True

            await session.disconnect()

            assert session._connected is False
            assert session._session is None
            assert session._connection is None


# ============================================================================
# Session Status Lifecycle Tests
# ============================================================================


class TestSessionStatusEnum:
    """Test SessionStatus enum definition and categories."""

    def test_all_statuses_defined(self):
        """Ensure all expected statuses are defined."""
        expected_statuses = {
            "INITIALIZING",
            "CONNECTING",
            "AUTHENTICATING",
            "AUTH_PENDING",
            "CONNECTED",
            "DISCONNECTING",
            "DISCONNECTED",
            "AUTH_FAILED",
            "CONNECTION_FAILED",
            "SERVER_UNREACHABLE",
            "FAILED",
            "RECONNECTING"
        }

        actual_statuses = {status.name for status in SessionStatus}
        assert expected_statuses == actual_statuses

    def test_status_categories_defined(self):
        """Ensure all status categories are defined."""
        assert hasattr(SessionStatusCategory, "ACTIVE_STATES")
        assert hasattr(SessionStatusCategory, "DISCONNECTED_STATES")
        assert hasattr(SessionStatusCategory, "FAILURE_STATES")
        assert hasattr(SessionStatusCategory, "PENDING_STATES")
        assert hasattr(SessionStatusCategory, "TERMINAL_STATES")
        assert hasattr(SessionStatusCategory, "RECOVERABLE_STATES")

    def test_active_states_category(self):
        """Validate ACTIVE_STATES category."""
        expected = {
            SessionStatus.CONNECTING,
            SessionStatus.AUTHENTICATING,
            SessionStatus.CONNECTED,
            SessionStatus.RECONNECTING
        }
        assert SessionStatusCategory.ACTIVE_STATES == expected

    def test_failure_states_category(self):
        """Validate FAILURE_STATES category."""
        expected = {
            SessionStatus.AUTH_FAILED,
            SessionStatus.CONNECTION_FAILED,
            SessionStatus.SERVER_UNREACHABLE,
            SessionStatus.FAILED
        }
        assert SessionStatusCategory.FAILURE_STATES == expected

    def test_recoverable_states_category(self):
        """Validate RECOVERABLE_STATES category."""
        expected = {
            SessionStatus.CONNECTION_FAILED,
            SessionStatus.SERVER_UNREACHABLE,
            SessionStatus.AUTH_FAILED
        }
        assert SessionStatusCategory.RECOVERABLE_STATES == expected


class TestSessionInitialStatus:
    """Test session initialization and initial status."""

    def test_initial_status(self):
        """Session should start in INITIALIZING state."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        assert session.status == SessionStatus.INITIALIZING

    def test_not_connected_initially(self):
        """Session should not be connected initially."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        assert not session.connected
        assert not session.is_operational


class TestSessionStatusProperties:
    """Test session status properties."""

    def test_is_operational_only_when_connected(self):
        """is_operational should only be True when CONNECTED."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        session.status = SessionStatus.INITIALIZING
        assert not session.is_operational

        session.status = SessionStatus.CONNECTING
        assert not session.is_operational

        session.status = SessionStatus.AUTHENTICATING
        assert not session.is_operational

        session.status = SessionStatus.CONNECTED
        assert session.is_operational

        session.status = SessionStatus.DISCONNECTED
        assert not session.is_operational

    def test_is_connecting(self):
        """is_connecting should be True for connection states."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        connecting_states = [
            SessionStatus.CONNECTING,
            SessionStatus.AUTHENTICATING,
            SessionStatus.RECONNECTING
        ]

        for status in SessionStatus:
            session.status = status
            if status in connecting_states:
                assert session.is_connecting, f"{status} should be connecting"
            else:
                assert not session.is_connecting, f"{status} should not be connecting"

    def test_requires_user_action(self):
        """requires_user_action should be True only for AUTH_PENDING."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        for status in SessionStatus:
            session.status = status
            if status == SessionStatus.AUTH_PENDING:
                assert session.requires_user_action
            else:
                assert not session.requires_user_action

    def test_can_retry(self):
        """can_retry should be True for recoverable states."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        recoverable = SessionStatusCategory.RECOVERABLE_STATES

        for status in SessionStatus:
            session.status = status
            if status in recoverable:
                assert session.can_retry, f"{status} should be retryable"
            else:
                assert not session.can_retry, f"{status} should not be retryable"

    def test_is_failed(self):
        """is_failed should be True for failure states."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        failure_states = SessionStatusCategory.FAILURE_STATES

        for status in SessionStatus:
            session.status = status
            if status in failure_states:
                assert session.is_failed, f"{status} should be failed"
            else:
                assert not session.is_failed, f"{status} should not be failed"


class TestConnectionErrorClassification:
    """Test error classification into appropriate status."""

    def test_classify_network_errors(self):
        """Network errors should map to SERVER_UNREACHABLE."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        network_errors = [
            Exception("connection refused"),
            Exception("connection timed out"),
            Exception("name or service not known"),
            Exception("no route to host"),
            Exception("network unreachable"),
            Exception("getaddrinfo failed"),
        ]

        for error in network_errors:
            status = session._classify_connection_error(error)
            assert status == SessionStatus.SERVER_UNREACHABLE, \
                f"{error} should map to SERVER_UNREACHABLE"

    def test_classify_auth_errors(self):
        """Auth errors should map to AUTH_FAILED."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        auth_errors = [
            Exception("unauthorized"),
            Exception("authentication failed"),
            Exception("invalid credentials"),
            Exception("forbidden"),
        ]

        for error in auth_errors:
            status = session._classify_connection_error(error)
            assert status == SessionStatus.AUTH_FAILED, \
                f"{error} should map to AUTH_FAILED"

    def test_classify_connection_lost_errors(self):
        """Connection lost errors should map to CONNECTION_FAILED."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        connection_lost_errors = [
            Exception("connection closed"),
            Exception("connection lost"),
            Exception("broken pipe"),
            Exception("EOF"),
        ]

        for error in connection_lost_errors:
            status = session._classify_connection_error(error)
            assert status == SessionStatus.CONNECTION_FAILED, \
                f"{error} should map to CONNECTION_FAILED"

    def test_classify_generic_error(self):
        """Generic errors should map to FAILED."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        generic_error = Exception("something went wrong")
        status = session._classify_connection_error(generic_error)
        assert status == SessionStatus.FAILED


class TestSessionStatusTransitions:
    """Test status transitions during session lifecycle."""

    @pytest.mark.asyncio
    async def test_successful_connection_no_auth(self):
        """Test status transitions for successful connection without auth."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        # Mock successful connection
        mock_connection = MagicMock()
        mock_connection.start = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_connection.stop = AsyncMock()

        mock_client_session = MagicMock()
        mock_client_session.__aenter__ = AsyncMock(return_value=mock_client_session)
        mock_client_session.__aexit__ = AsyncMock()
        mock_client_session.initialize = AsyncMock()

        with patch('keycardai.mcp.client.session.create_connection', return_value=mock_connection), \
             patch('keycardai.mcp.client.session.ClientSession', return_value=mock_client_session):

            # Initial state
            assert session.status == SessionStatus.INITIALIZING

            # Connect
            await session.connect()

            # Should end in CONNECTED state
            assert session.status == SessionStatus.CONNECTED
            assert session.is_operational

    @pytest.mark.asyncio
    async def test_connection_with_auth_pending(self):
        """Test status transitions when auth is pending."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        # Mock connection that triggers auth
        mock_connection = MagicMock()
        mock_connection.start = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_connection.stop = AsyncMock()

        mock_client_session = MagicMock()
        mock_client_session.__aenter__ = AsyncMock(return_value=mock_client_session)
        mock_client_session.__aexit__ = AsyncMock()
        mock_client_session.initialize = AsyncMock(side_effect=Exception("auth required"))

        # Mock get_auth_challenge to return a challenge
        session.get_auth_challenge = AsyncMock(return_value={
            "authorization_url": "https://example.com/auth",
            "state": "abc123"
        })

        with patch('keycardai.mcp.client.session.create_connection', return_value=mock_connection), \
             patch('keycardai.mcp.client.session.ClientSession', return_value=mock_client_session):

            # Connect
            await session.connect()

            # Should end in AUTH_PENDING state
            assert session.status == SessionStatus.AUTH_PENDING
            assert session.requires_user_action

    @pytest.mark.asyncio
    async def test_connection_failure_server_unreachable(self):
        """Test status transitions for server unreachable."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        # Mock connection that fails with network error
        mock_connection = MagicMock()
        mock_connection.start = AsyncMock(
            side_effect=Exception("connection refused")
        )
        mock_connection.stop = AsyncMock()

        with patch('keycardai.mcp.client.session.create_connection', return_value=mock_connection):
            session.get_auth_challenge = AsyncMock(return_value=None)

            # Attempt to connect - should not raise, sets status instead
            await session.connect()

            # Should end in SERVER_UNREACHABLE state
            assert session.status == SessionStatus.SERVER_UNREACHABLE
            assert session.is_failed
            assert session.can_retry

    @pytest.mark.asyncio
    async def test_graceful_disconnect_status_transitions(self):
        """Test status transitions for graceful disconnect."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        # Set up connected session
        session._connected = True
        session.status = SessionStatus.CONNECTED

        mock_client_session = MagicMock()
        mock_client_session.__aexit__ = AsyncMock()
        session._session = mock_client_session

        mock_connection = MagicMock()
        mock_connection.stop = AsyncMock()
        session._connection = mock_connection

        # Disconnect
        await session.disconnect()

        # Should end in DISCONNECTED state
        assert session.status == SessionStatus.DISCONNECTED
        assert not session.connected

    @pytest.mark.asyncio
    async def test_disconnect_from_disconnected_is_safe(self):
        """Test that disconnect from DISCONNECTED is safe."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        session.status = SessionStatus.DISCONNECTED
        session._connected = False

        # Should be no-op
        await session.disconnect()
        assert session.status == SessionStatus.DISCONNECTED


class TestSessionStatusLogging:
    """Test status change logging."""

    def test_status_change_logs(self):
        """Test that status changes are logged."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        with patch('keycardai.mcp.client.session.logger') as mock_logger:
            session._set_status(SessionStatus.CONNECTING, "test reason")

            # Should log the transition
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]

            assert "test_server" in call_args
            assert "initializing" in call_args  # old status
            assert "connecting" in call_args  # new status
            assert "test reason" in call_args


class TestSessionStatusBackwardCompatibility:
    """Test backward compatibility with existing code."""

    def test_connected_property_still_works(self):
        """The 'connected' property should still work."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        session._connected = False
        assert not session.connected

        session._connected = True
        assert session.connected

    @pytest.mark.asyncio
    async def test_requires_auth_method_still_works(self):
        """The requires_auth() method should still work."""
        storage = InMemoryBackend()
        coordinator = LocalAuthCoordinator(backend=storage)
        context = coordinator.create_context("test-context")

        server_config = {"url": "http://localhost:3000", "transport": "http"}
        session = Session("test_server", server_config, context, coordinator)

        session.get_auth_challenge = AsyncMock(return_value=None)
        assert not await session.requires_auth()

        session.get_auth_challenge = AsyncMock(return_value={"state": "test"})
        assert await session.requires_auth()

