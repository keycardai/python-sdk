"""Test seams for tools built against keycardai-hermes.

    from keycardai.hermes.testing import mock_access_context

    with mock_access_context(resource_tokens={"https://api.example.com": "tok"}):
        result = my_tool_impl(query="hello")
"""

from .test_utils import mock_access_context, override_access_context

__all__ = ["mock_access_context", "override_access_context"]
