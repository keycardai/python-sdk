"""The integrations must read a tool's input schema off a real mcp.types.Tool.

mcp 2.0 renamed inputSchema -> input_schema while keeping the camelCase spelling
as a construction alias. So a read of the old name does not raise, it just
misses, and every integration guards that read with hasattr and falls back to
{} -- turning the miss into a silently empty schema handed to an agent
framework rather than an error.

These tests use a real Tool, not a mock, on purpose: hasattr is unconditionally
True on a MagicMock, so a mock cannot tell the two spellings apart and cannot
catch this regression.
"""

from unittest.mock import MagicMock

import pytest
from mcp.types import Tool

SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
}


@pytest.fixture
def tool() -> Tool:
    """Built via the camelCase alias, which mcp 2.0 still accepts."""
    return Tool(name="get_weather", description="Weather", inputSchema=SCHEMA)


def test_snake_case_attribute_is_the_readable_one(tool: Tool) -> None:
    assert tool.input_schema == SCHEMA


def test_camel_case_attribute_does_not_resolve(tool: Tool) -> None:
    """The exact shape of the bug: no raise, so a hasattr guard fails silently."""
    assert not hasattr(tool, "inputSchema")


def test_langchain_conversion_carries_the_schema_through(tool: Tool) -> None:
    """Exercises the real converter, not a re-implementation of its expression.

    A regression here means the agent receives a tool with no arguments, which
    is why asserting on the generated args_schema matters rather than asserting
    the read in isolation.
    """
    pytest.importorskip("langchain")
    from keycardai.mcp.client.integrations.langchain_agents import LangChainClient

    client = LangChainClient(MagicMock())
    converted = client._convert_mcp_tool_to_langchain(tool, "test-server")

    assert converted.name == "get_weather"
    assert "city" in converted.args_schema.model_fields, (
        "input schema was dropped; the agent would see a tool taking no arguments"
    )
