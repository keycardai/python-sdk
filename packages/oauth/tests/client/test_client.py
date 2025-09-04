"""Tests for the unified OAuth client implementation."""

from unittest.mock import AsyncMock, patch

import pytest

from keycardai.oauth import AsyncClient, Client, ClientConfig
from keycardai.oauth.types.models import AuthorizationServerMetadata


class TestSyncClientContextManager:
    """Test sync client context manager behavior."""

    def test_sync_client_context_manager_calls_ensure_initialized(self):
        """Test that sync client context manager calls _ensure_initialized."""
        config = ClientConfig(
            enable_metadata_discovery=False,
            auto_register_client=False,
        )

        client = Client(
            base_url="https://test.example.com",
            config=config,
        )

        with patch.object(client, '_ensure_initialized') as mock_init:
            with client:
                # Verify _ensure_initialized was called during context entry
                mock_init.assert_called_once()

    def test_sync_client_context_manager_initializes_with_discovery(self):
        """Test that sync client properly initializes when discovery is enabled."""
        mock_metadata = AuthorizationServerMetadata(
            issuer="https://test.example.com",
            authorization_endpoint="https://test.example.com/auth",
            token_endpoint="https://test.example.com/token",
            jwks_uri="https://test.example.com/.well-known/jwks.json",
        )

        config = ClientConfig(
            enable_metadata_discovery=True,
            auto_register_client=False,
        )

        client = Client(
            base_url="https://test.example.com",
            config=config,
        )

        with patch.object(client, 'discover_server_metadata', return_value=mock_metadata):
            with client:
                # Client should be initialized after entering context
                assert client._initialized is True
                assert client._discovered_endpoints is not None

    def test_sync_client_context_manager_without_discovery(self):
        """Test sync client context manager when discovery is disabled."""
        config = ClientConfig(
            enable_metadata_discovery=False,
            auto_register_client=False,
        )

        client = Client(
            base_url="https://test.example.com",
            config=config,
        )

        with client:
            # Client should be initialized even without discovery
            assert client._initialized is True

    def test_sync_client_lazy_initialization_without_context_manager(self):
        """Test that sync client doesn't initialize without context manager or explicit calls."""
        config = ClientConfig(
            enable_metadata_discovery=False,
            auto_register_client=False,
        )

        client = Client(
            base_url="https://test.example.com",
            config=config,
        )

        assert client._initialized is False

        with patch.object(client, '_ensure_initialized') as mock_init:
            _ = client.client_id  # This should trigger initialization
            mock_init.assert_called_once()


class TestAsyncClientContextManager:
    """Test async client context manager behavior for comparison."""

    @pytest.mark.asyncio
    async def test_async_client_context_manager_calls_ensure_initialized(self):
        """Test that async client context manager calls _ensure_initialized."""
        config = ClientConfig(
            enable_metadata_discovery=False,
            auto_register_client=False,
        )

        client = AsyncClient(
            base_url="https://test.example.com",
            config=config,
        )

        with patch.object(client, '_ensure_initialized', new_callable=AsyncMock) as mock_init:
            async with client:
                # Verify _ensure_initialized was called during context entry
                mock_init.assert_called_once()


class TestClientInitializationParity:
    """Test that sync and async clients have consistent initialization behavior."""

    def test_sync_and_async_client_initialization_parity(self):
        """Test that sync and async clients initialize consistently."""
        config = ClientConfig(
            enable_metadata_discovery=False,
            auto_register_client=False,
        )

        sync_client = Client(
            base_url="https://test.example.com",
            config=config,
        )

        async_client = AsyncClient(
            base_url="https://test.example.com",
            config=config,
        )

        assert sync_client._initialized is False
        assert async_client._initialized is False

        with patch.object(sync_client, '_ensure_initialized'):
            with sync_client:
                pass
