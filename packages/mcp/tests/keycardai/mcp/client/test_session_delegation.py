"""
Tests for Session delegation to mcp.ClientSession.

Verifies that:
1. Custom methods work correctly (list_tools, call_tool)
2. Delegated methods are accessible via __getattr__
3. Connection state is properly checked
4. Errors are raised appropriately
"""

from unittest.mock import AsyncMock, Mock

import pytest

from keycardai.mcp.client.context import Context
from keycardai.mcp.client.session import Session
from keycardai.mcp.client.storage import InMemoryBackend, NamespacedStorage


@pytest.fixture
def mock_context():
    """Create a mock context."""
    backend = InMemoryBackend()
    storage = NamespacedStorage(backend, "test")
    return Context(
        id="test-context",
        storage=storage,
        coordinator=Mock()
    )


@pytest.fixture
def session(mock_context):
    """Create a session for testing."""
    server_config = {
        "url": "http://localhost:8000",
        "transport": "http",
    }
    return Session(
        server_name="test-server",
        server_config=server_config,
        context=mock_context,
        coordinator=Mock()
    )


class TestSessionDelegation:
    """Test Session delegation to mcp.ClientSession."""

    def test_getattr_raises_when_not_connected(self, session):
        """Test that accessing methods before connect raises error."""
        with pytest.raises(RuntimeError, match="session not connected"):
            _ = session.send_ping

    def test_getattr_raises_for_nonexistent_attribute(self, session):
        """Test that accessing non-existent attributes raises AttributeError."""
        # Create a mock session so we can test __getattr__
        session._session = Mock(spec=[])  # Empty spec means no attributes
        session._connected = True

        with pytest.raises(AttributeError, match="object has no attribute 'nonexistent_method'"):
            _ = session.nonexistent_method

    @pytest.mark.asyncio
    async def test_list_tools_now_delegated(self, session, mock_context):
        """Test that list_tools now delegates to underlying ClientSession."""
        # Mock the underlying session
        from mcp import Tool
        from mcp.types import ListToolsResult
        mock_mcp_session = AsyncMock()
        mock_mcp_session.list_tools = AsyncMock(
            return_value=ListToolsResult(
                tools=[
                    Tool(name="tool1", description="First tool", inputSchema={"type": "object"}),
                    Tool(name="tool2", description="Second tool", inputSchema={"type": "object"})
                ],
                nextCursor=None
            )
        )

        session._session = mock_mcp_session
        session._connected = True

        # Call list_tools - should delegate to underlying session
        result = await session.list_tools()

        # Verify delegation happened (called once, no pagination)
        assert mock_mcp_session.list_tools.call_count == 1
        # Result is ListToolsResult, not processed list
        assert isinstance(result, ListToolsResult)
        assert len(result.tools) == 2
        assert result.tools[0].name == "tool1"
        assert result.tools[1].name == "tool2"

    @pytest.mark.asyncio
    async def test_delegated_method_accessible(self, session):
        """Test that delegated methods are accessible via __getattr__."""
        # Mock the underlying session
        mock_mcp_session = AsyncMock()
        mock_mcp_session.send_ping = AsyncMock(return_value=Mock(result="pong"))

        session._session = mock_mcp_session
        session._connected = True

        # Access delegated method
        send_ping = session.send_ping
        assert callable(send_ping)

        # Call it
        result = await send_ping()
        assert result.result == "pong"
        mock_mcp_session.send_ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_delegated_method_checks_connection(self, session):
        """Test that delegated methods check connection state."""
        # Mock the underlying session
        mock_mcp_session = AsyncMock()
        mock_mcp_session.send_ping = AsyncMock()

        session._session = mock_mcp_session
        session._connected = False  # Not connected

        # Try to call delegated method
        send_ping = session.send_ping

        with pytest.raises(RuntimeError, match="session not connected"):
            await send_ping()

        # Verify the underlying method was never called
        mock_mcp_session.send_ping.assert_not_called()

    @pytest.mark.asyncio
    async def test_delegated_method_with_args(self, session):
        """Test that delegated methods correctly pass arguments."""
        # Mock the underlying session
        mock_mcp_session = AsyncMock()
        mock_mcp_session.list_resources = AsyncMock(
            return_value=Mock(resources=[Mock(uri="test://resource")])
        )

        session._session = mock_mcp_session
        session._connected = True

        # Call delegated method with arguments
        params = Mock()
        result = await session.list_resources(params=params)

        # Verify arguments were passed correctly
        mock_mcp_session.list_resources.assert_called_once_with(params=params)
        assert result.resources[0].uri == "test://resource"

    @pytest.mark.asyncio
    async def test_multiple_delegated_methods(self, session):
        """Test that multiple different delegated methods work."""
        # Mock the underlying session
        mock_mcp_session = AsyncMock()
        mock_mcp_session.send_ping = AsyncMock(return_value=Mock(result="pong"))
        mock_mcp_session.list_prompts = AsyncMock(return_value=Mock(prompts=[]))
        mock_mcp_session.get_prompt = AsyncMock(return_value=Mock(name="test"))

        session._session = mock_mcp_session
        session._connected = True

        # Call different delegated methods
        await session.send_ping()
        await session.list_prompts()
        await session.get_prompt("test")

        # Verify all were called
        mock_mcp_session.send_ping.assert_called_once()
        mock_mcp_session.list_prompts.assert_called_once()
        mock_mcp_session.get_prompt.assert_called_once_with("test")

    def test_custom_methods_exist(self, session):
        """Test that custom methods are defined directly (not delegated)."""
        # These should exist as real methods on Session, not via __getattr__
        assert hasattr(Session, 'connect')
        assert hasattr(Session, 'disconnect')
        assert hasattr(Session, 'requires_auth')
        assert hasattr(Session, 'get_auth_challenge')

    @pytest.mark.asyncio
    async def test_delegation_preserves_return_types(self, session):
        """Test that delegation preserves return types correctly."""
        # Mock the underlying session
        expected_result = Mock(
            resources=[
                Mock(uri="test://resource1", name="Resource 1"),
                Mock(uri="test://resource2", name="Resource 2"),
            ],
            nextCursor="next_page"
        )

        mock_mcp_session = AsyncMock()
        mock_mcp_session.list_resources = AsyncMock(return_value=expected_result)

        session._session = mock_mcp_session
        session._connected = True

        # Call delegated method
        result = await session.list_resources()

        # Verify the exact object is returned (not wrapped or modified)
        assert result is expected_result
        assert len(result.resources) == 2
        assert result.nextCursor == "next_page"

