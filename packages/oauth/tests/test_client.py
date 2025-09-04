"""Tests for the unified OAuth client implementation."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from keycardai.oauth import AsyncClient, Client, ClientConfig
from keycardai.oauth.types.models import (
    AuthorizationServerMetadata,
    ClientRegistrationRequest,
    ServerMetadataRequest,
    TokenExchangeRequest,
)
from keycardai.oauth.types.oauth import GrantType, ResponseType, TokenEndpointAuthMethod


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


class TestOverloadEquivalence:
    """Test that all overload forms create equivalent function calls."""

    def test_register_client_overload_equivalence(self):
        """Test that register_client overloads create equivalent calls."""
        # Test data
        test_data = {
            "client_name": "TestApp",
            "redirect_uris": ["https://app.com/callback"],
            "jwks_uri": "https://example.com/.well-known/jwks.json",
            "scope": "openid profile email",
            "grant_types": [GrantType.AUTHORIZATION_CODE, GrantType.REFRESH_TOKEN],
            "response_types": [ResponseType.CODE],
            "token_endpoint_auth_method": TokenEndpointAuthMethod.CLIENT_SECRET_BASIC,
            "additional_metadata": {"policy_uri": "https://app.com/privacy"},
            "client_uri": "https://app.com",
            "logo_uri": "https://app.com/logo.png",
            "tos_uri": "https://app.com/tos",
            "policy_uri": "https://app.com/privacy",
            "software_id": "test-software",
            "software_version": "1.0.0",
            "timeout": 30.0,
        }
        
        # Create the request object
        request_obj = ClientRegistrationRequest(**test_data)
        
        with patch('keycardai.oauth.client.register_client') as mock_register:
            mock_register.return_value = Mock()
            client = Client("https://test.keycard.cloud")
            
            # Method 1: Using request object
            client.register_client(request_obj)
            call1_args = mock_register.call_args
            
            # Method 2: Using kwargs
            mock_register.reset_mock()
            client.register_client(**test_data)
            call2_args = mock_register.call_args
            
            # Compare the requests passed to the underlying function
            request1 = call1_args[0][0]  # First argument (request) from first call
            request2 = call2_args[0][0]  # First argument (request) from second call
            
            # Convert both to dict for comparison
            dict1 = request1.model_dump(exclude_none=True)
            dict2 = request2.model_dump(exclude_none=True)
            
            assert dict1 == dict2, f"Requests differ: {dict1} != {dict2}"

    @pytest.mark.asyncio
    async def test_async_register_client_overload_equivalence(self):
        """Test that async register_client overloads create equivalent calls."""
        test_data = {
            "client_name": "TestApp",
            "jwks_uri": "https://example.com/.well-known/jwks.json",
            "scope": "openid profile",
        }
        
        request_obj = ClientRegistrationRequest(**test_data)
        
        with patch('keycardai.oauth.client.register_client_async') as mock_register_async:
            mock_register_async.return_value = Mock()
            async_client = AsyncClient("https://test.keycard.cloud")
            
            # Method 1: Using request object
            await async_client.register_client(request_obj)
            call1_args = mock_register_async.call_args
            
            # Method 2: Using kwargs
            mock_register_async.reset_mock()
            await async_client.register_client(**test_data)
            call2_args = mock_register_async.call_args
            
            # Compare the requests
            request1 = call1_args[0][0]
            request2 = call2_args[0][0]
            
            dict1 = request1.model_dump(exclude_none=True)
            dict2 = request2.model_dump(exclude_none=True)
            
            assert dict1 == dict2, f"Async requests differ: {dict1} != {dict2}"

    def test_token_exchange_overload_equivalence(self):
        """Test that token_exchange overloads create equivalent calls."""
        test_data = {
            "subject_token": "user_token_123",
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "audience": "api.microservice.company.com",
            "actor_token": "service_token_456",
            "actor_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "scope": "read write",
            "resource": "https://api.company.com/data",
            "timeout": 15.0,
        }
        
        request_obj = TokenExchangeRequest(**test_data)
        
        with patch('keycardai.oauth.client.token_exchange') as mock_exchange:
            mock_exchange.return_value = Mock()
            client = Client("https://test.keycard.cloud")
            
            # Method 1: Using request object
            client.token_exchange(request_obj)
            call1_args = mock_exchange.call_args
            
            # Method 2: Using kwargs
            mock_exchange.reset_mock()
            client.token_exchange(**test_data)
            call2_args = mock_exchange.call_args
            
            # Compare the requests
            request1 = call1_args[0][0]
            request2 = call2_args[0][0]
            
            dict1 = request1.model_dump(exclude_none=True)
            dict2 = request2.model_dump(exclude_none=True)
            
            assert dict1 == dict2, f"Token exchange requests differ: {dict1} != {dict2}"

    @pytest.mark.asyncio
    async def test_async_token_exchange_overload_equivalence(self):
        """Test that async token_exchange overloads create equivalent calls."""
        test_data = {
            "subject_token": "user_token_123",
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "audience": "api.microservice.company.com",
        }
        
        request_obj = TokenExchangeRequest(**test_data)
        
        with patch('keycardai.oauth.client.token_exchange_async') as mock_exchange_async:
            mock_exchange_async.return_value = Mock()
            async_client = AsyncClient("https://test.keycard.cloud")
            
            # Method 1: Using request object
            await async_client.token_exchange(request_obj)
            call1_args = mock_exchange_async.call_args
            
            # Method 2: Using kwargs
            mock_exchange_async.reset_mock()
            await async_client.token_exchange(**test_data)
            call2_args = mock_exchange_async.call_args
            
            # Compare the requests
            request1 = call1_args[0][0]
            request2 = call2_args[0][0]
            
            dict1 = request1.model_dump(exclude_none=True)
            dict2 = request2.model_dump(exclude_none=True)
            
            assert dict1 == dict2, f"Async token exchange requests differ: {dict1} != {dict2}"

    def test_discover_server_metadata_overload_equivalence(self):
        """Test that discover_server_metadata overloads create equivalent calls."""
        test_base_url = "https://custom.auth.server.com"
        request_obj = ServerMetadataRequest(base_url=test_base_url)
        
        with patch('keycardai.oauth.client.discover_server_metadata') as mock_discover:
            mock_discover.return_value = Mock()
            client = Client("https://test.keycard.cloud")
            
            # Method 1: Using request object
            client.discover_server_metadata(request_obj)
            call1_args = mock_discover.call_args
            
            # Method 2: Using kwargs
            mock_discover.reset_mock()
            client.discover_server_metadata(base_url=test_base_url)
            call2_args = mock_discover.call_args
            
            # The discovery functions are called with request=request, context=context
            request1 = call1_args.kwargs['request']
            request2 = call2_args.kwargs['request']
            
            dict1 = request1.model_dump(exclude_none=True)
            dict2 = request2.model_dump(exclude_none=True)
            
            assert dict1 == dict2, f"Discovery requests differ: {dict1} != {dict2}"

    @pytest.mark.asyncio
    async def test_async_discover_server_metadata_overload_equivalence(self):
        """Test that async discover_server_metadata overloads create equivalent calls."""
        test_base_url = "https://custom.auth.server.com"
        request_obj = ServerMetadataRequest(base_url=test_base_url)
        
        with patch('keycardai.oauth.client.discover_server_metadata_async') as mock_discover_async:
            mock_discover_async.return_value = Mock()
            async_client = AsyncClient("https://test.keycard.cloud")
            
            # Method 1: Using request object
            await async_client.discover_server_metadata(request_obj)
            call1_args = mock_discover_async.call_args
            
            # Method 2: Using kwargs
            mock_discover_async.reset_mock()
            await async_client.discover_server_metadata(base_url=test_base_url)
            call2_args = mock_discover_async.call_args
            
            # Compare the requests (discovery functions are called with keyword args)
            request1 = call1_args.kwargs['request']
            request2 = call2_args.kwargs['request']
            
            dict1 = request1.model_dump(exclude_none=True)
            dict2 = request2.model_dump(exclude_none=True)
            
            assert dict1 == dict2, f"Async discovery requests differ: {dict1} != {dict2}"

    def test_error_handling_mixed_arguments(self):
        """Test that passing both request and kwargs raises appropriate errors."""
        client = Client("https://test.keycard.cloud")
        
        # Test register_client error handling
        request = ClientRegistrationRequest(client_name="Test")
        with pytest.raises(TypeError, match="both"):
            client.register_client(request, client_name="Another")
        
        # Test token_exchange error handling
        request = TokenExchangeRequest(
            subject_token="token",
            subject_token_type="urn:ietf:params:oauth:token-type:access_token"
        )
        with pytest.raises(TypeError, match="both"):
            client.token_exchange(request, subject_token="another")
        
        # Test discover_server_metadata error handling
        request = ServerMetadataRequest(base_url="https://test.com")
        with pytest.raises(TypeError, match="both"):
            client.discover_server_metadata(request, base_url="https://other.com")

    @pytest.mark.asyncio 
    async def test_async_error_handling_mixed_arguments(self):
        """Test that async methods properly reject mixed arguments."""
        async_client = AsyncClient("https://test.keycard.cloud")
        
        # Test async register_client error handling
        request = ClientRegistrationRequest(client_name="Test")
        with pytest.raises(TypeError, match="both"):
            await async_client.register_client(request, client_name="Another")
        
        # Test async token_exchange error handling
        request = TokenExchangeRequest(
            subject_token="token",
            subject_token_type="urn:ietf:params:oauth:token-type:access_token"
        )
        with pytest.raises(TypeError, match="both"):
            await async_client.token_exchange(request, subject_token="another")
        
        # Test async discover_server_metadata error handling
        request = ServerMetadataRequest(base_url="https://test.com")
        with pytest.raises(TypeError, match="both"):
            await async_client.discover_server_metadata(request, base_url="https://other.com")
