"""The testing seam: exercise tools with no capability, zone, or network."""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from keycardai.pydantic_ai import ResourceAccessError, get_access_context
from keycardai.pydantic_ai.testing import mock_access_context

RESOURCE = "https://api.example.test"


def call_api() -> str:
    """Call the API with the delegated token."""
    access = get_access_context()
    if access.has_error():
        return f"unavailable: {access.get_error()['message']}"
    return access.access(RESOURCE).access_token


# Row 27: the seam serves a tool run by the test model, with no capability on the agent.
def test_resource_tokens_are_served_to_a_tool_run_by_the_test_model() -> None:
    agent = Agent(TestModel(call_tools=["call_api"]))
    agent.tool_plain(call_api)
    with mock_access_context(resource_tokens={RESOURCE: "tok-123"}):
        result = agent.run_sync("go")
    assert "tok-123" in str(result.output)


# Row 28
def test_any_resource_token_is_a_convenience_with_a_tradeoff() -> None:
    with mock_access_context(access_token="tok-any"):
        assert call_api() == "tok-any"


# Row 29
def test_global_error_is_visible_to_the_tool() -> None:
    with mock_access_context(error_message="no identity for this run"):
        assert call_api() == "unavailable: no identity for this run"


# Row 30
def test_resource_error_raises_only_on_access() -> None:
    with mock_access_context(resource_errors={RESOURCE: "not granted"}) as access:
        assert access.has_errors()
        assert not access.has_error()
        with pytest.raises(ResourceAccessError):
            access.access(RESOURCE)


# Row 31
def test_outside_the_seam_the_tool_reports_a_missing_capability() -> None:
    with pytest.raises(RuntimeError, match="KeycardGrant"):
        call_api()
