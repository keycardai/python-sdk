"""Unit tests for the Client class.

This module tests the Client class initialization, lifecycle management,
connection handling, and coordination with sessions.
"""

import pytest
from mcp import Tool

from keycardai.mcp.client.auth.coordinators.base import AuthCoordinator
from keycardai.mcp.client.auth.coordinators.local import LocalAuthCoordinator
from keycardai.mcp.client.client import Client
from keycardai.mcp.client.context import Context
from keycardai.mcp.client.exceptions import (
    ClientConfigurationError,
    ToolNotFoundException,
)
from keycardai.mcp.client.session import Session, SessionStatus
from keycardai.mcp.client.storage import InMemoryBackend
from keycardai.mcp.client.types import ToolInfo


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

class MockUnavailableSession(Session):
    """Mock session that is not available."""

    def __init__(self, server_name: str, server_config, context, coordinator):
        super().__init__(server_name, server_config, context, coordinator)
        self._connected = False
        self.connect_called = False

    async def connect(self, _retry_after_auth: bool = True):
        self.connect_called = True
        self.status = SessionStatus.CONNECTED

    async def disconnect(self):
        self.disconnect_called = True

    async def list_tools(self, params=None):
        raise Exception("Session is not available")

    async def call_tool(self, tool_name: str, arguments):
        raise Exception("Session is not available")

# Mock Session for testing
class MockSession(Session):
    """Mock session that tracks method calls."""

    def __init__(self, server_name: str, server_config, context, coordinator):
        super().__init__(server_name, server_config, context, coordinator)
        self.connect_called = False
        self.disconnect_called = False
        self.call_tool_calls = []
        self.get_auth_challenge_result = None
        self.mock_tools = []  # Can be set by tests

    async def connect(self, _retry_after_auth: bool = True):
        self.connect_called = True
        self._connected = True

    async def disconnect(self):
        self.disconnect_called = True
        self._connected = False

    async def list_tools(self, params=None):
        """Return mock tools in ListToolsResult format."""
        from mcp.types import ListToolsResult
        return ListToolsResult(tools=self.mock_tools, nextCursor=None)

    async def call_tool(self, tool_name: str, arguments):
        self.call_tool_calls.append((tool_name, arguments))
        return {"result": f"called {tool_name}"}

    async def get_auth_challenge(self):
        return self.get_auth_challenge_result


class TestClientInitialization:
    """Test Client initialization with various configurations."""

    def test_default_client_initialization(self):
        """Test that a default client is created with minimal config."""
        servers = {
            "test_server": {
                "url": "http://localhost:3000"
            }
        }

        client = Client(servers=servers)

        assert client._context_id is not None
        assert len(client._context_id) > 0

        assert isinstance(client.auth_coordinator, LocalAuthCoordinator)

        assert client.context is not None
        assert isinstance(client.context, Context)

        assert "test_server" in client.sessions
        assert isinstance(client.sessions["test_server"], Session)

    def test_custom_coordinator_is_used(self):
        """Test that custom coordinator is used when provided."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        custom_coordinator = LocalAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=custom_coordinator)

        assert client.auth_coordinator is custom_coordinator

    def test_default_coordinator_is_local(self):
        """Test that default coordinator is LocalAuthCoordinator."""
        servers = {"test_server": {"url": "http://localhost:3000"}}

        client = Client(servers=servers)

        assert isinstance(client.auth_coordinator, LocalAuthCoordinator)

    def test_client_with_custom_context_id(self):
        """Test client initialization with custom context_id."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        custom_context_id = "user:alice"

        client = Client(servers=servers, context_id=custom_context_id)

        assert client._context_id == custom_context_id
        assert client.context.id == custom_context_id

    def test_custom_auth_coordinator_injection(self):
        """Test that custom auth coordinator is properly used."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        custom_backend = InMemoryBackend()
        custom_coordinator = LocalAuthCoordinator(backend=custom_backend)

        client = Client(servers=servers, auth_coordinator=custom_coordinator)

        assert client.auth_coordinator is custom_coordinator
        # Storage is a NamespacedStorage wrapping the backend
        assert client.auth_coordinator.storage is not None

    def test_multiple_server_configs(self):
        """Test client initialization with multiple servers."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"},
            "server3": {"url": "http://localhost:3002"}
        }

        client = Client(servers=servers)

        assert len(client.sessions) == 3
        assert "server1" in client.sessions
        assert "server2" in client.sessions
        assert "server3" in client.sessions

        assert client.sessions["server1"].server_name == "server1"
        assert client.sessions["server2"].server_name == "server2"
        assert client.sessions["server3"].server_name == "server3"

    def test_no_side_effects_on_creation(self):
        """Test that creating a client has no side effects."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Verify no async operations were performed
        assert mock_coordinator.start_called is False
        assert mock_coordinator.shutdown_called is False

        # Verify sessions were created but not connected
        assert len(client.sessions) == 1
        assert client.sessions["test_server"]._connected is False

    def test_context_id_and_context_mutually_exclusive(self):
        """Test that providing both context_id and context raises ClientConfigurationError."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        coordinator = LocalAuthCoordinator()
        context = coordinator.create_context("test_context")

        with pytest.raises(ClientConfigurationError, match="Cannot provide both 'context_id' and 'context' parameters"):
            Client(
                servers=servers,
                context_id="another_context",
                context=context
            )

    def test_client_with_pre_built_context(self):
        """Test client initialization with pre-built context (advanced usage)."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        coordinator = LocalAuthCoordinator()
        context = coordinator.create_context("custom_context")

        client = Client(
            servers=servers,
            auth_coordinator=coordinator,
            context=context
        )

        assert client.context is context
        assert client.context.id == "custom_context"


class TestClientContextManager:
    """Test Client context manager behavior."""

    @pytest.mark.asyncio
    async def test_context_manager_connects_to_servers(self):
        """Test that entering context manager connects to all servers."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2

        async with client:
            assert mock_session1.connect_called is True
            assert mock_session2.connect_called is True

    @pytest.mark.asyncio
    async def test_context_manager_handles_unavailable_sessions(self):
        """Test that context manager handles unavailable sessions."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        mock_session = MockUnavailableSession("test_server", servers["test_server"], client.context, mock_coordinator)
        client.sessions["test_server"] = mock_session

        async with client:
            assert mock_session.connect_called is True
            assert client.sessions["test_server"].status == SessionStatus.CONNECTED

    @pytest.mark.asyncio
    async def test_context_manager_returns_client(self):
        """Test that context manager returns the client instance."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        mock_session = MockSession("test_server", servers["test_server"], client.context, mock_coordinator)
        client.sessions["test_server"] = mock_session

        async with client as returned_client:
            assert returned_client is client

    @pytest.mark.asyncio
    async def test_context_manager_disconnects_on_exit(self):
        """Test that exiting context manager disconnects from servers."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2

        async with client:
            pass

        assert mock_session1.disconnect_called is True
        assert mock_session2.disconnect_called is True

class TestClientConnect:
    """Test Client connect method with various scenarios."""

    @pytest.mark.asyncio
    async def test_connect_all_servers_by_default(self):
        """Test that connect() without arguments connects to all servers."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"},
            "server3": {"url": "http://localhost:3002"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace sessions with mock sessions
        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        mock_session3 = MockSession("server3", servers["server3"], client.context, mock_coordinator)
        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2
        client.sessions["server3"] = mock_session3

        await client.connect()

        # Should call connect for all sessions
        assert mock_session1.connect_called is True
        assert mock_session2.connect_called is True
        assert mock_session3.connect_called is True

    @pytest.mark.asyncio
    async def test_connect_specific_server(self):
        """Test that connect(server='name') only connects to specified server."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"},
            "server3": {"url": "http://localhost:3002"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace sessions with mock sessions
        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        mock_session3 = MockSession("server3", servers["server3"], client.context, mock_coordinator)
        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2
        client.sessions["server3"] = mock_session3

        await client.connect(server="server2")

        # Should only connect to server2
        assert mock_session1.connect_called is False
        assert mock_session2.connect_called is True
        assert mock_session3.connect_called is False

    @pytest.mark.asyncio
    async def test_connect_with_force_reconnect_disconnects_first(self):
        """Test that force_reconnect=True disconnects before connecting."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace session with mock that tracks call order
        mock_session = MockSession("test_server", servers["test_server"], client.context, mock_coordinator)
        client.sessions["test_server"] = mock_session

        await client.connect(force_reconnect=True)

        # Should disconnect first, then connect
        assert mock_session.disconnect_called is True
        assert mock_session.connect_called is True

    @pytest.mark.asyncio
    async def test_connect_without_force_reconnect_skips_disconnect(self):
        """Test that connect without force_reconnect does not disconnect."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace session with mock
        mock_session = MockSession("test_server", servers["test_server"], client.context, mock_coordinator)
        client.sessions["test_server"] = mock_session

        await client.connect(force_reconnect=False)

        # Should only connect, not disconnect
        assert mock_session.disconnect_called is False
        assert mock_session.connect_called is True

    @pytest.mark.asyncio
    async def test_connect_specific_server_with_force_reconnect(self):
        """Test that force_reconnect works with specific server."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace sessions with mock sessions
        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2

        await client.connect(server="server1", force_reconnect=True)

        # Should disconnect and connect only server1
        assert mock_session1.disconnect_called is True
        assert mock_session1.connect_called is True
        assert mock_session2.disconnect_called is False
        assert mock_session2.connect_called is False

    @pytest.mark.asyncio
    async def test_connect_multiple_times_is_safe(self):
        """Test that calling connect multiple times is safe (idempotent behavior)."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Use a counter to track connect calls
        connect_count = 0

        class CountingMockSession(MockSession):
            async def connect(self, _retry_after_auth: bool = True):
                nonlocal connect_count
                connect_count += 1
                await super().connect(_retry_after_auth)

        mock_session = CountingMockSession("test_server", servers["test_server"], client.context, mock_coordinator)
        client.sessions["test_server"] = mock_session

        # Connect multiple times
        await client.connect()
        await client.connect()
        await client.connect()

        # Each call should attempt to connect (Session handles idempotency)
        assert connect_count == 3


class TestClientDisconnect:
    """Test Client disconnect method."""

    @pytest.mark.asyncio
    async def test_disconnect_all_sessions(self):
        """Test that disconnect() disconnects all sessions."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"},
            "server3": {"url": "http://localhost:3002"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace sessions with mock sessions
        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        mock_session3 = MockSession("server3", servers["server3"], client.context, mock_coordinator)
        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2
        client.sessions["server3"] = mock_session3

        await client.disconnect()

        # Should call disconnect for all sessions
        assert mock_session1.disconnect_called is True
        assert mock_session2.disconnect_called is True
        assert mock_session3.disconnect_called is True

    @pytest.mark.asyncio
    async def test_disconnect_with_no_sessions(self):
        """Test that disconnect() works even with no sessions."""
        servers = {}
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)
        # Should not raise any errors
        await client.disconnect()


class TestClientAddServer:
    """Test Client add_server method."""

    def test_add_server_creates_session(self):
        """Test that add_server creates a new session."""
        servers = {"server1": {"url": "http://localhost:3000"}}
        client = Client(servers=servers)

        assert len(client.sessions) == 1

        # Add a new server
        client.add_server("server2", {"url": "http://localhost:3001"})

        assert len(client.sessions) == 2
        assert "server2" in client.sessions
        assert client.sessions["server2"].server_name == "server2"

    def test_add_server_uses_client_context(self):
        """Test that add_server creates session with client's context."""
        servers = {"server1": {"url": "http://localhost:3000"}}
        client = Client(servers=servers)

        client.add_server("server2", {"url": "http://localhost:3001"})

        # New session should use same context
        assert client.sessions["server2"].context is client.context

    def test_add_server_uses_client_coordinator(self):
        """Test that add_server creates session with client's coordinator."""
        servers = {"server1": {"url": "http://localhost:3000"}}
        client = Client(servers=servers)

        client.add_server("server2", {"url": "http://localhost:3001"})

        # New session should use same coordinator
        assert client.sessions["server2"].coordinator is client.auth_coordinator


class TestClientListTools:
    """Test Client list_tools method (returns list[ToolInfo])."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_tool_info_list(self):
        """Test that list_tools returns a list of ToolInfo objects."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace session with mock and add mock tools
        mock_session = MockSession("test_server", servers["test_server"], client.context, mock_coordinator)
        mock_session.mock_tools = [
            Tool(name="tool1", description="First tool", inputSchema={"type": "object"}),
            Tool(name="tool2", description="Second tool", inputSchema={"type": "object"}),
        ]
        mock_session._connected = True
        client.sessions["test_server"] = mock_session

        result = await client.list_tools()

        # Should return list of ToolInfo objects
        assert isinstance(result, list)
        assert len(result) == 2
        assert isinstance(result[0], ToolInfo)
        assert isinstance(result[1], ToolInfo)
        assert result[0].tool.name == "tool1"
        assert result[0].server == "test_server"
        assert result[1].tool.name == "tool2"
        assert result[1].server == "test_server"

    @pytest.mark.asyncio
    async def test_list_tools_with_multiple_servers(self):
        """Test that list_tools returns tools from multiple servers with server info."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace sessions with mock sessions
        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session1.mock_tools = [
            Tool(name="server1_tool", description="Tool from server 1", inputSchema={"type": "object"}),
        ]
        mock_session1._connected = True
        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        mock_session2.mock_tools = [
            Tool(name="server2_tool", description="Tool from server 2", inputSchema={"type": "object"}),
        ]
        mock_session2._connected = True
        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2

        result = await client.list_tools()

        # Should return list with tools from both servers
        assert len(result) == 2

        # Find tools by server
        tools_by_server = {info.server: info.tool for info in result}
        assert "server1" in tools_by_server
        assert "server2" in tools_by_server
        assert tools_by_server["server1"].name == "server1_tool"
        assert tools_by_server["server2"].name == "server2_tool"

    @pytest.mark.asyncio
    async def test_list_tools_specific_server(self):
        """Test that list_tools can filter by server name."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace sessions with mock sessions
        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session1.mock_tools = [
            Tool(name="server1_tool", description="Tool from server 1", inputSchema={"type": "object"}),
        ]
        mock_session1._connected = True
        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        mock_session2.mock_tools = [
            Tool(name="server2_tool", description="Tool from server 2", inputSchema={"type": "object"}),
        ]
        mock_session2._connected = True
        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2

        # List tools from server2 only
        result = await client.list_tools(server_name="server2")

        # Should only return tools from server2
        assert len(result) == 1
        assert isinstance(result[0], ToolInfo)
        assert result[0].server == "server2"
        assert result[0].tool.name == "server2_tool"

    @pytest.mark.asyncio
    async def test_list_tools_tool_info_attributes(self):
        """Test that ToolInfo provides proper attribute access."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace session with mock and add mock tool
        mock_session = MockSession("test_server", servers["test_server"], client.context, mock_coordinator)
        mock_session.mock_tools = [
            Tool(name="test_tool", description="Test description", inputSchema={"type": "object"}),
        ]
        mock_session._connected = True
        client.sessions["test_server"] = mock_session

        result = await client.list_tools()

        # Should be able to access tool and server via attributes
        tool_info = result[0]
        assert tool_info.tool.name == "test_tool"
        assert tool_info.tool.description == "Test description"
        assert tool_info.server == "test_server"

    @pytest.mark.asyncio
    async def test_list_tools_empty_when_no_tools(self):
        """Test that list_tools returns empty list when no tools available."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace session with mock that has no tools
        mock_session = MockSession("test_server", servers["test_server"], client.context, mock_coordinator)
        mock_session.mock_tools = []
        mock_session._connected = True
        client.sessions["test_server"] = mock_session

        result = await client.list_tools()

        # Should return empty list
        assert result == []


class TestClientCallTool:
    """Test Client call_tool method."""

    @pytest.mark.asyncio
    async def test_call_tool_with_explicit_server(self):
        """Test that call_tool delegates to the correct session when server is specified."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace session with mock
        mock_session = MockSession("test_server", servers["test_server"], client.context, mock_coordinator)
        mock_session._connected = True
        client.sessions["test_server"] = mock_session

        result = await client.call_tool(
            tool_name="my_tool",
            arguments={"arg1": "value1"},
            server_name="test_server"
        )

        # Should delegate to session
        assert len(mock_session.call_tool_calls) == 1
        assert mock_session.call_tool_calls[0] == ("my_tool", {"arg1": "value1"})
        assert result == {"result": "called my_tool"}

    @pytest.mark.asyncio
    async def test_call_tool_with_multiple_servers_explicit(self):
        """Test that call_tool routes to correct server when explicitly specified."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace sessions with mock sessions
        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session1._connected = True
        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        mock_session2._connected = True
        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2

        # Call tool on server2
        result = await client.call_tool(
            tool_name="test_tool",
            arguments={},
            server_name="server2"
        )

        # Should only call server2
        assert len(mock_session1.call_tool_calls) == 0
        assert len(mock_session2.call_tool_calls) == 1
        assert mock_session2.call_tool_calls[0] == ("test_tool", {})
        assert result == {"result": "called test_tool"}

    @pytest.mark.asyncio
    async def test_call_tool_auto_discovery(self):
        """Test that call_tool can automatically discover which server has the tool."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace sessions with mock sessions
        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session1.mock_tools = [
            Tool(name="server1_tool", description="Tool from server 1", inputSchema={"type": "object"}),
        ]
        mock_session1._connected = True
        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        mock_session2.mock_tools = [
            Tool(name="server2_tool", description="Tool from server 2", inputSchema={"type": "object"}),
        ]
        mock_session2._connected = True
        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2

        # Call tool without specifying server - should auto-discover
        result = await client.call_tool(
            tool_name="server2_tool",
            arguments={"test": "data"}
        )

        # Should have called server2
        assert len(mock_session2.call_tool_calls) == 1
        assert mock_session2.call_tool_calls[0] == ("server2_tool", {"test": "data"})
        assert result == {"result": "called server2_tool"}

    @pytest.mark.asyncio
    async def test_call_tool_auto_discovery_first_match(self):
        """Test that auto-discovery uses the first matching tool found."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace sessions with mock sessions - both have the same tool name
        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session1.mock_tools = [
            Tool(name="common_tool", description="Tool from server 1", inputSchema={"type": "object"}),
        ]
        mock_session1._connected = True
        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        mock_session2.mock_tools = [
            Tool(name="common_tool", description="Tool from server 2", inputSchema={"type": "object"}),
        ]
        mock_session2._connected = True
        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2

        # Call tool without specifying server
        result = await client.call_tool(
            tool_name="common_tool",
            arguments={}
        )

        # Should have called one of them (first match in iteration order)
        total_calls = len(mock_session1.call_tool_calls) + len(mock_session2.call_tool_calls)
        assert total_calls == 1
        assert result["result"] == "called common_tool"

    @pytest.mark.asyncio
    async def test_call_tool_not_found_raises_error(self):
        """Test that call_tool raises ToolNotFoundException when tool is not found."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace session with mock that has no tools
        mock_session = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session.mock_tools = []
        mock_session._connected = True
        client.sessions["server1"] = mock_session

        # Call tool that doesn't exist should raise ToolNotFoundException
        with pytest.raises(ToolNotFoundException) as exc_info:
            await client.call_tool(
                tool_name="nonexistent_tool",
                arguments={}
            )

        # Verify exception details
        exception = exc_info.value
        assert exception.tool_name == "nonexistent_tool"
        assert "server1" in exception.searched_servers
        assert "not found on any server" in str(exception)

    @pytest.mark.asyncio
    async def test_call_tool_not_found_with_available_tools(self):
        """Test that ToolNotFoundException includes helpful information about available tools."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace session with mock that has some tools
        mock_session = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session.mock_tools = [
            Tool(name="available_tool_1", description="First tool", inputSchema={"type": "object"}),
            Tool(name="available_tool_2", description="Second tool", inputSchema={"type": "object"}),
        ]
        mock_session._connected = True
        client.sessions["server1"] = mock_session

        # Call tool that doesn't exist
        with pytest.raises(ToolNotFoundException) as exc_info:
            await client.call_tool(
                tool_name="nonexistent_tool",
                arguments={}
            )

        # Verify exception includes available tools
        exception = exc_info.value
        assert exception.tool_name == "nonexistent_tool"
        assert len(exception.available_tools) == 2
        assert "available_tool_1" in exception.available_tools
        assert "available_tool_2" in exception.available_tools


class TestClientGetAuthChallenges:
    """Test Client get_auth_challenges method."""

    @pytest.mark.asyncio
    async def test_get_auth_challenges_from_all_servers(self):
        """Test that get_auth_challenges collects challenges from all servers."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace sessions with mock sessions
        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session1.get_auth_challenge_result = {
            "authorization_url": "http://auth1.com",
            "state": "state1"
        }
        mock_session1._connected = True
        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        mock_session2.get_auth_challenge_result = {
            "authorization_url": "http://auth2.com",
            "state": "state2"
        }
        mock_session2._connected = True
        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2

        challenges = await client.get_auth_challenges()

        # Should return challenges from both servers
        assert len(challenges) == 2
        assert challenges[0]["server"] == "server1"
        assert challenges[0]["authorization_url"] == "http://auth1.com"
        assert challenges[1]["server"] == "server2"
        assert challenges[1]["authorization_url"] == "http://auth2.com"

    @pytest.mark.asyncio
    async def test_get_auth_challenges_from_specific_server(self):
        """Test that get_auth_challenges can target specific server."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace sessions with mock sessions
        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session1.get_auth_challenge_result = {"authorization_url": "http://auth1.com"}

        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        mock_session2.get_auth_challenge_result = {"authorization_url": "http://auth2.com"}

        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2

        challenges = await client.get_auth_challenges(server_name="server1")

        # Should only return challenge from server1
        assert len(challenges) == 1
        assert challenges[0]["server"] == "server1"

    @pytest.mark.asyncio
    async def test_get_auth_challenges_filters_none_values(self):
        """Test that get_auth_challenges filters out None challenges."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"},
            "server3": {"url": "http://localhost:3002"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace sessions with mock sessions
        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session1.get_auth_challenge_result = {"authorization_url": "http://auth1.com"}

        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)
        mock_session2.get_auth_challenge_result = None  # No challenge

        mock_session3 = MockSession("server3", servers["server3"], client.context, mock_coordinator)
        mock_session3.get_auth_challenge_result = {"authorization_url": "http://auth3.com"}

        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2
        client.sessions["server3"] = mock_session3

        challenges = await client.get_auth_challenges()

        # Should only return non-None challenges
        assert len(challenges) == 2
        assert all(c["server"] in ["server1", "server3"] for c in challenges)

    @pytest.mark.asyncio
    async def test_get_auth_challenges_returns_empty_list_when_none(self):
        """Test that get_auth_challenges returns empty list when no challenges."""
        servers = {
            "server1": {"url": "http://localhost:3000"},
            "server2": {"url": "http://localhost:3001"}
        }
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace sessions with mock sessions (both return None)
        mock_session1 = MockSession("server1", servers["server1"], client.context, mock_coordinator)
        mock_session2 = MockSession("server2", servers["server2"], client.context, mock_coordinator)

        client.sessions["server1"] = mock_session1
        client.sessions["server2"] = mock_session2

        challenges = await client.get_auth_challenges()

        # Should return empty list
        assert challenges == []

    @pytest.mark.asyncio
    async def test_get_auth_challenges_includes_server_name(self):
        """Test that each challenge includes the server name."""
        servers = {"test_server": {"url": "http://localhost:3000"}}
        mock_coordinator = MockAuthCoordinator()

        client = Client(servers=servers, auth_coordinator=mock_coordinator)

        # Replace session with mock
        mock_session = MockSession("test_server", servers["test_server"], client.context, mock_coordinator)
        mock_session.get_auth_challenge_result = {
            "authorization_url": "http://auth.com",
            "state": "abc123",
            "other_field": "value"
        }

        client.sessions["test_server"] = mock_session

        challenges = await client.get_auth_challenges()

        # Challenge should include all original fields plus 'server'
        assert len(challenges) == 1
        assert challenges[0]["server"] == "test_server"
        assert challenges[0]["authorization_url"] == "http://auth.com"
        assert challenges[0]["state"] == "abc123"
        assert challenges[0]["other_field"] == "value"


class TestClientIdGeneration:
    """Test Client ID generation."""

    def test_generate_client_id_creates_unique_ids(self):
        """Test that _generate_client_id creates unique IDs."""
        servers = {"test_server": {"url": "http://localhost:3000"}}

        client1 = Client(servers=servers)
        client2 = Client(servers=servers)

        # IDs should be unique
        assert client1._context_id != client2._context_id

    def test_generate_client_id_length(self):
        """Test that generated IDs have expected length."""
        servers = {"test_server": {"url": "http://localhost:3000"}}

        client = Client(servers=servers)

        # nanoid with size=16 should generate 16-character IDs
        assert len(client._context_id) == 16

