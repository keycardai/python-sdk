"""Testing utilities for FastMCP integration with Keycard authentication.

This module provides mock implementations and utilities for testing FastMCP servers
that use Keycard authentication without requiring real OAuth flows or network calls.

Components:
- mock_access_context: Context manager for mocking authentication in tests

Example:
    from keycardai.mcp.integrations.fastmcp.testing import mock_access_context

    # Test successful authentication with default token
    with mock_access_context():
        # Your test code here - will return "test_access_token" for any resource

    # Test with specific access token
    with mock_access_context(access_token="my_custom_token"):
        # Your test code here - will return "my_custom_token" for any resource

    # Test with resource-specific tokens
    with mock_access_context(resource_tokens={
        "https://api.example.com": "token_123",
        "https://api.other.com": "token_456"
    }):
        # Your test code here - will return specific tokens for each resource

    # Test error scenarios
    with mock_access_context(has_errors=True, error_message="Auth failed"):
        # Your test code here - access_context.has_errors() will return True
"""

from .test_utils import mock_access_context

__all__ = [
    "mock_access_context",
]
