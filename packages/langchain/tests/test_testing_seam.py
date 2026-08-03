"""The testing seam: exercise tools with no middleware, zone, or network."""

from __future__ import annotations

import pytest
from langchain.tools import tool

from keycardai.langchain import ResourceAccessError, get_access_context
from keycardai.langchain.testing import mock_access_context

RESOURCE = "https://api.example.test"


@tool
def call_api() -> str:
    """Call the API with the delegated token."""
    access = get_access_context()
    if access.has_error():
        return f"unavailable: {access.get_error()['message']}"
    return access.access(RESOURCE).access_token


def test_resource_tokens_are_served_per_resource() -> None:
    with mock_access_context(resource_tokens={RESOURCE: "tok-123"}):
        assert call_api.invoke({}) == "tok-123"


def test_any_resource_token_is_a_convenience_with_a_tradeoff() -> None:
    """The bare form serves any resource, so it cannot catch a wrong URL."""
    with mock_access_context(access_token="tok-any"):
        assert call_api.invoke({}) == "tok-any"


def test_global_error_is_visible_to_the_tool() -> None:
    with mock_access_context(error_message="no identity for this run"):
        assert call_api.invoke({}) == "unavailable: no identity for this run"


def test_resource_error_raises_only_on_access() -> None:
    with mock_access_context(resource_errors={RESOURCE: "not granted"}) as access:
        assert access.has_errors()
        assert not access.has_error()  # per-resource, not global
        with pytest.raises(ResourceAccessError):
            access.access(RESOURCE)


def test_outside_the_seam_the_tool_reports_a_missing_middleware() -> None:
    with pytest.raises(RuntimeError, match="KeycardGrantMiddleware"):
        call_api.invoke({})
