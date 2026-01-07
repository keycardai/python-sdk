"""Tests for Pydantic AI integration."""

from unittest.mock import AsyncMock, MagicMock

import pytest

# Note: These tests require pydantic-ai to be installed
# Run with: uv run --with pydantic-ai pytest tests/

pytest.importorskip("pydantic_ai")


def test_import_pydantic_ai_integration() -> None:
    """Test that the Pydantic AI integration can be imported."""
    from keycardai.mcp.client.integrations import pydantic_agents

    assert pydantic_agents is not None
    assert hasattr(pydantic_agents, "PydanticAIClient")
    assert hasattr(pydantic_agents, "create_client")


def test_import_pydantic_ai_client() -> None:
    """Test that PydanticAIClient can be imported directly."""
    from keycardai.mcp.client.integrations.pydantic_agents import (
        PydanticAIClient,
        create_client,
    )

    assert PydanticAIClient is not None
    assert create_client is not None


def test_pydantic_agents_in_integrations_all() -> None:
    """Test that pydantic_agents is exported in integrations __all__."""
    from keycardai.mcp.client import integrations

    assert hasattr(integrations, "__all__")
    assert "pydantic_agents" in integrations.__all__


def test_create_client_returns_pydantic_ai_client() -> None:
    """Test that create_client returns a PydanticAIClient instance."""
    from keycardai.mcp.client.integrations.pydantic_agents import (
        PydanticAIClient,
        create_client,
    )

    mock_mcp_client = MagicMock()
    client = create_client(mock_mcp_client)

    assert isinstance(client, PydanticAIClient)


def test_pydantic_ai_client_init() -> None:
    """Test PydanticAIClient initialization."""
    from keycardai.mcp.client.integrations.pydantic_agents import PydanticAIClient

    mock_mcp_client = MagicMock()
    client = PydanticAIClient(mock_mcp_client)

    assert client._mcp_client is mock_mcp_client
    assert client._pending_challenges == []
    assert client._authenticated_servers == []
    assert client._tools_cache == []


def test_get_system_prompt_no_challenges() -> None:
    """Test get_system_prompt returns base instructions when no auth needed."""
    from keycardai.mcp.client.integrations.pydantic_agents import PydanticAIClient

    mock_mcp_client = MagicMock()
    client = PydanticAIClient(mock_mcp_client)
    client._pending_challenges = []

    base_prompt = "You are a helpful assistant."
    result = client.get_system_prompt(base_prompt)

    assert result == base_prompt


def test_get_system_prompt_with_challenges() -> None:
    """Test get_system_prompt adds auth section when auth needed."""
    from keycardai.mcp.client.integrations.pydantic_agents import PydanticAIClient

    mock_mcp_client = MagicMock()
    client = PydanticAIClient(mock_mcp_client)
    client._pending_challenges = [{"server": "test-service"}]

    base_prompt = "You are a helpful assistant."
    result = client.get_system_prompt(base_prompt)

    assert base_prompt in result
    assert "AUTHENTICATION STATUS" in result
    assert "test-service" in result


def test_get_system_prompt_custom_auth_prompt() -> None:
    """Test get_system_prompt uses custom auth prompt when provided."""
    from keycardai.mcp.client.integrations.pydantic_agents import PydanticAIClient

    mock_mcp_client = MagicMock()
    client = PydanticAIClient(mock_mcp_client, auth_prompt="Custom auth instructions")
    client._pending_challenges = [{"server": "test-service"}]

    base_prompt = "You are a helpful assistant."
    result = client.get_system_prompt(base_prompt)

    assert base_prompt in result
    assert "Custom auth instructions" in result


@pytest.mark.asyncio
async def test_get_tools_empty_when_no_authenticated_servers() -> None:
    """Test get_tools returns empty list when no servers authenticated."""
    from keycardai.mcp.client.integrations.pydantic_agents import PydanticAIClient

    mock_mcp_client = MagicMock()
    client = PydanticAIClient(mock_mcp_client)
    client._authenticated_servers = []

    tools = await client.get_tools()

    assert tools == []


@pytest.mark.asyncio
async def test_get_tools_converts_mcp_tools() -> None:
    """Test get_tools converts MCP tools to Pydantic AI tools."""
    from pydantic_ai import Tool

    from keycardai.mcp.client.integrations.pydantic_agents import PydanticAIClient

    # Create a mock MCP tool
    mock_tool = MagicMock()
    mock_tool.name = "test_tool"
    mock_tool.description = "A test tool"
    mock_tool.inputSchema = {"properties": {}, "required": []}

    # Create mock tool info
    mock_tool_info = MagicMock()
    mock_tool_info.tool = mock_tool
    mock_tool_info.server = "test-server"

    mock_mcp_client = MagicMock()
    mock_mcp_client.list_tools = AsyncMock(return_value=[mock_tool_info])

    client = PydanticAIClient(mock_mcp_client)
    client._authenticated_servers = ["test-server"]

    tools = await client.get_tools()

    assert len(tools) == 1
    assert isinstance(tools[0], Tool)
    assert tools[0].name == "test_tool"


@pytest.mark.asyncio
async def test_get_auth_tools_empty_when_no_challenges() -> None:
    """Test get_auth_tools returns empty list when no auth needed."""
    from keycardai.mcp.client.integrations.pydantic_agents import PydanticAIClient

    mock_mcp_client = MagicMock()
    client = PydanticAIClient(mock_mcp_client)
    client._pending_challenges = []

    auth_tools = await client.get_auth_tools()

    assert auth_tools == []


@pytest.mark.asyncio
async def test_get_auth_tools_returns_tool_when_challenges_exist() -> None:
    """Test get_auth_tools returns auth tool when auth needed."""
    from pydantic_ai import Tool

    from keycardai.mcp.client.integrations.pydantic_agents import PydanticAIClient

    mock_mcp_client = MagicMock()
    client = PydanticAIClient(mock_mcp_client)
    client._pending_challenges = [
        {"server": "test-service", "authorization_url": "https://example.com/auth"}
    ]

    auth_tools = await client.get_auth_tools()

    assert len(auth_tools) == 1
    assert isinstance(auth_tools[0], Tool)
    assert auth_tools[0].name == "request_authentication"


@pytest.mark.asyncio
async def test_context_manager_connects_client() -> None:
    """Test that entering context manager connects the MCP client."""
    from keycardai.mcp.client.integrations.pydantic_agents import PydanticAIClient

    mock_mcp_client = MagicMock()
    mock_mcp_client.connect = AsyncMock()
    mock_mcp_client.get_auth_challenges = AsyncMock(return_value=[])
    mock_mcp_client.list_tools = AsyncMock(return_value=[])

    client = PydanticAIClient(mock_mcp_client)

    async with client as c:
        assert c is client

    mock_mcp_client.connect.assert_awaited_once()
    mock_mcp_client.get_auth_challenges.assert_awaited_once()


def test_json_type_to_python() -> None:
    """Test JSON schema type conversion to Python types."""
    from keycardai.mcp.client.integrations.pydantic_agents import PydanticAIClient

    mock_mcp_client = MagicMock()
    client = PydanticAIClient(mock_mcp_client)

    assert client._json_type_to_python("string") is str
    assert client._json_type_to_python("number") is float
    assert client._json_type_to_python("integer") is int
    assert client._json_type_to_python("boolean") is bool
    assert client._json_type_to_python("array") is list
    assert client._json_type_to_python("object") is dict
    assert client._json_type_to_python("unknown") is str  # default


def test_schema_to_pydantic() -> None:
    """Test JSON schema to Pydantic model conversion."""
    from pydantic import BaseModel

    from keycardai.mcp.client.integrations.pydantic_agents import PydanticAIClient

    mock_mcp_client = MagicMock()
    client = PydanticAIClient(mock_mcp_client)

    schema = {
        "properties": {
            "name": {"type": "string", "description": "The name"},
            "age": {"type": "integer", "description": "The age"},
        },
        "required": ["name"],
    }

    model = client._schema_to_pydantic(schema, "test_tool")

    assert issubclass(model, BaseModel)
    assert "name" in model.model_fields
    assert "age" in model.model_fields
