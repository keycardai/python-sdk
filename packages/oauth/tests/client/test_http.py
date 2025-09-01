"""Tests for HTTP client implementations."""

import pytest

from keycardai.oauth.client.http import AsyncHTTPClient, HTTPClient, HTTPClientProtocol


class TestAsyncHTTPClient:
    """Test AsyncHTTPClient implementation."""

    def test_init(self):
        """Test client initialization with defaults."""
        client = AsyncHTTPClient()

        assert client.timeout == 30.0
        assert client.verify_ssl is True
        assert client.max_retries == 3
        assert client.user_agent == "KeyCardAI-OAuth/0.0.1"

    def test_init_custom_config(self):
        """Test client initialization with custom configuration."""
        client = AsyncHTTPClient(
            timeout=60.0, verify_ssl=False, max_retries=5, user_agent="Custom/1.0"
        )

        assert client.timeout == 60.0
        assert client.verify_ssl is False
        assert client.max_retries == 5
        assert client.user_agent == "Custom/1.0"

    @pytest.mark.asyncio
    async def test_aclose(self):
        """Test aclose method exists for context manager support."""
        client = AsyncHTTPClient()
        await client.aclose()  # Should not raise an exception

    def test_request_method_exists(self):
        """Test that request method exists with proper signature."""
        client = AsyncHTTPClient()

        # Should have request method
        assert hasattr(client, "request")
        assert callable(client.request)


class TestHTTPClient:
    """Test HTTPClient implementation."""

    def test_init(self):
        """Test client initialization with defaults."""
        client = HTTPClient()

        assert client.timeout == 30.0
        assert client.verify_ssl is True
        assert client.max_retries == 3
        assert client.user_agent == "KeyCardAI-OAuth/0.0.1"

    def test_init_custom_config(self):
        """Test client initialization with custom configuration."""
        client = HTTPClient(
            timeout=60.0, verify_ssl=False, max_retries=5, user_agent="Custom/1.0"
        )

        assert client.timeout == 60.0
        assert client.verify_ssl is False
        assert client.max_retries == 5
        assert client.user_agent == "Custom/1.0"

    def test_close(self):
        """Test close method for context manager support."""
        client = HTTPClient()
        client.close()  # Should not raise an exception

    def test_request_method_exists(self):
        """Test that request method exists with proper signature."""
        client = HTTPClient()

        # Should have request method
        assert hasattr(client, "request")
        assert callable(client.request)

    def test_context_manager_support(self):
        """Test that HTTPClient can be used as a context manager."""
        # This should not raise any exceptions
        with HTTPClient() as client:
            assert isinstance(client, HTTPClient)

    def test_compatible_with_operations(self):
        """Test that HTTPClient is compatible with operations module."""
        from keycardai.oauth.client.operations import introspect_token, register_client

        # These should be importable without errors
        assert callable(introspect_token)
        assert callable(register_client)

    def test_has_required_methods(self):
        """Test that HTTPClient has all required methods."""
        client = HTTPClient()

        required_methods = ["request", "close"]
        for method in required_methods:
            assert hasattr(client, method), (
                f"HTTPClient missing required method: {method}"
            )
            assert callable(getattr(client, method)), (
                f"HTTPClient.{method} is not callable"
            )


class TestHTTPClientProtocol:
    """Test HTTPClientProtocol interface."""

    def test_protocol_exists(self):
        """Test that HTTPClientProtocol is properly defined."""
        # This test ensures the protocol is importable and has the expected interface
        assert hasattr(HTTPClientProtocol, "__annotations__")

        # Check that both implementations conform to the protocol
        async_client = AsyncHTTPClient()
        sync_client = HTTPClient()

        # Both should have request methods
        assert hasattr(async_client, "request")
        assert hasattr(sync_client, "request")


class TestCombinedHTTPClients:
    """Test that both HTTP clients work together in the combined module."""

    def test_both_clients_importable(self):
        """Test that both clients can be imported from the same module."""
        from keycardai.oauth.client.http import (
            AsyncHTTPClient,
            HTTPClient,
        )

        # Should be able to create instances of both
        async_client = AsyncHTTPClient()
        sync_client = HTTPClient()

        assert isinstance(async_client, AsyncHTTPClient)
        assert isinstance(sync_client, HTTPClient)

    def test_clients_have_same_interface(self):
        """Test that both clients have compatible interfaces."""
        async_client = AsyncHTTPClient()
        sync_client = HTTPClient()

        # Both should have the same configuration options
        assert async_client.timeout == sync_client.timeout
        assert async_client.verify_ssl == sync_client.verify_ssl
        assert async_client.max_retries == sync_client.max_retries
        assert async_client.user_agent == sync_client.user_agent
