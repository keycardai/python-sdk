"""Test seams for agents built with keycardai-pydantic-ai.

Lets tests exercise tools without a zone, a network, or real token exchange.

    from keycardai.pydantic_ai.testing import mock_access_context

    with mock_access_context(resource_tokens={"https://api.example.com": "tok"}):
        result = my_tool_impl(query="hello")
"""

from .test_utils import mock_access_context, override_access_context

__all__ = ["mock_access_context", "override_access_context"]
