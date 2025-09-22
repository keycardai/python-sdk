"""Tests for the unified OAuth client implementation."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError

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

    def test_exchange_token_overload_equivalence(self):
        """Test that exchange_token overloads create equivalent calls."""
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

        with patch('keycardai.oauth.client.exchange_token') as mock_exchange:
            mock_exchange.return_value = Mock()
            client = Client("https://test.keycard.cloud")

            # Method 1: Using request object
            client.exchange_token(request_obj)
            call1_args = mock_exchange.call_args

            # Method 2: Using kwargs
            mock_exchange.reset_mock()
            client.exchange_token(**test_data)
            call2_args = mock_exchange.call_args

            # Compare the requests
            request1 = call1_args[0][0]
            request2 = call2_args[0][0]

            dict1 = request1.model_dump(exclude_none=True)
            dict2 = request2.model_dump(exclude_none=True)

            assert dict1 == dict2, f"Exchange token requests differ: {dict1} != {dict2}"

    @pytest.mark.asyncio
    async def test_async_exchange_token_overload_equivalence(self):
        """Test that async exchange_token overloads create equivalent calls."""
        test_data = {
            "subject_token": "user_token_123",
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "audience": "api.microservice.company.com",
        }

        request_obj = TokenExchangeRequest(**test_data)

        with patch('keycardai.oauth.client.exchange_token_async') as mock_exchange_async:
            mock_exchange_async.return_value = Mock()
            async_client = AsyncClient("https://test.keycard.cloud")

            # Method 1: Using request object
            await async_client.exchange_token(request_obj)
            call1_args = mock_exchange_async.call_args

            # Method 2: Using kwargs
            mock_exchange_async.reset_mock()
            await async_client.exchange_token(**test_data)
            call2_args = mock_exchange_async.call_args

            # Compare the requests
            request1 = call1_args[0][0]
            request2 = call2_args[0][0]

            dict1 = request1.model_dump(exclude_none=True)
            dict2 = request2.model_dump(exclude_none=True)

            assert dict1 == dict2, f"Async exchange token requests differ: {dict1} != {dict2}"

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

        # Test exchange_token error handling
        request = TokenExchangeRequest(
            subject_token="token",
            subject_token_type="urn:ietf:params:oauth:token-type:access_token"
        )
        with pytest.raises(TypeError, match="both"):
            client.exchange_token(request, subject_token="another")

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

        # Test async exchange_token error handling
        request = TokenExchangeRequest(
            subject_token="token",
            subject_token_type="urn:ietf:params:oauth:token-type:access_token"
        )
        with pytest.raises(TypeError, match="both"):
            await async_client.exchange_token(request, subject_token="another")

        # Test async discover_server_metadata error handling
        request = ServerMetadataRequest(base_url="https://test.com")
        with pytest.raises(TypeError, match="both"):
            await async_client.discover_server_metadata(request, base_url="https://other.com")


class TestClientValidation:
    """Test client validation behavior with Pydantic models."""

    def test_register_client_empty_client_name_validation(self):
        """Test that register_client rejects empty client_name with TypeError."""
        client = Client("https://test.keycard.cloud", config=ClientConfig(enable_metadata_discovery=False, auto_register_client=False))

        # Test that empty string client_name raises TypeError (client-level validation)
        with pytest.raises(TypeError) as exc_info:
            client.register_client(client_name="")

        # Verify the error message
        assert "client_name is required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_async_register_client_empty_client_name_validation(self):
        """Test that async register_client rejects empty client_name with TypeError."""
        async_client = AsyncClient("https://test.keycard.cloud", config=ClientConfig(enable_metadata_discovery=False, auto_register_client=False))

        # Test that empty string client_name raises TypeError (client-level validation)
        with pytest.raises(TypeError) as exc_info:
            await async_client.register_client(client_name="")

        # Verify the error message
        assert "client_name is required" in str(exc_info.value)

    def test_register_client_whitespace_only_client_name_validation(self):
        """Test that register_client accepts whitespace-only client_name (passes client validation but may fail at server)."""
        client = Client("https://test.keycard.cloud", config=ClientConfig(enable_metadata_discovery=False, auto_register_client=False))

        # Whitespace-only client_name passes client-level validation (truthy string)
        # but would fail at the server level - this test just verifies it gets past client validation
        with patch('keycardai.oauth.client.register_client') as mock_register:
            mock_register.return_value = Mock()
            client.register_client(client_name="   ")
            # If we get here, client validation passed
            mock_register.assert_called_once()

    def test_pydantic_model_empty_client_name_validation(self):
        """Test that ClientRegistrationRequest model directly validates empty client_name."""
        # Test Pydantic validation directly on the model
        with pytest.raises(ValidationError) as exc_info:
            ClientRegistrationRequest(client_name="")

        # Verify the error is about string length
        error = exc_info.value
        assert len(error.errors()) == 1
        assert error.errors()[0]["type"] == "string_too_short"
        assert error.errors()[0]["loc"] == ("client_name",)
        assert "at least 1 character" in str(error.errors()[0]["msg"])

    def test_token_exchange_request_validation(self):
        """Test that TokenExchangeRequest model validates required fields and handles kwargs correctly."""
        # Test empty subject_token (min_length validation)
        with pytest.raises(ValidationError) as exc_info:
            TokenExchangeRequest(subject_token="")

        error = exc_info.value
        assert len(error.errors()) == 1
        assert error.errors()[0]["type"] == "string_too_short"
        assert error.errors()[0]["loc"] == ("subject_token",)
        assert "at least 1 character" in str(error.errors()[0]["msg"])

        # Test valid request with minimal required fields
        request = TokenExchangeRequest(subject_token="valid_token")
        assert request.subject_token == "valid_token"
        assert request.subject_token_type.value == "urn:ietf:params:oauth:token-type:access_token"  # Default value
        assert request.grant_type == "urn:ietf:params:oauth:grant-type:token-exchange"

        # Test that extra kwargs are ignored (Pydantic's default behavior)
        request = TokenExchangeRequest(
            subject_token="valid_token",
            unknown_field="should_be_ignored",
            another_unknown="also_ignored"
        )
        assert request.subject_token == "valid_token"
        assert not hasattr(request, "unknown_field")
        assert not hasattr(request, "another_unknown")

        # Test valid request with all optional fields
        from keycardai.oauth.types.oauth import TokenType
        request = TokenExchangeRequest(
            subject_token="valid_token",
            subject_token_type=TokenType.JWT,
            audience="api.example.com",
            resource="https://api.example.com/data",
            scope="read write",
            requested_token_type=TokenType.ACCESS_TOKEN,
            actor_token="actor_token",
            actor_token_type=TokenType.ACCESS_TOKEN,
            timeout=30.0,
            client_id="client123"
        )
        assert request.subject_token == "valid_token"
        assert request.subject_token_type == TokenType.JWT
        assert request.audience == "api.example.com"
        assert request.resource == "https://api.example.com/data"
        assert request.scope == "read write"
        assert request.requested_token_type == TokenType.ACCESS_TOKEN
        assert request.actor_token == "actor_token"
        assert request.actor_token_type == TokenType.ACCESS_TOKEN
        assert request.timeout == 30.0
        assert request.client_id == "client123"

    def test_default_user_agent_in_requests(self):
        """Test that default user agent is set correctly in all request types when not configured by user."""
        # Create client with default config (no custom user_agent)
        client = Client("https://test.keycard.cloud")

        # Test 1: Server metadata discovery request
        with patch('keycardai.oauth.client.build_http_context') as mock_build_context:
            with patch('keycardai.oauth.client.discover_server_metadata') as mock_discover:
                mock_discover.return_value = Mock()

                try:
                    client.discover_server_metadata(base_url="https://test.keycard.cloud")
                except Exception:
                    pass  # We just want to check the context building

                # Verify build_http_context was called with default user agent
                mock_build_context.assert_called()
                call_kwargs = mock_build_context.call_args.kwargs
                assert call_kwargs['user_agent'] == "KeyCardAI-OAuth/0.0.1"

        # Test 2: Client registration request
        with patch('keycardai.oauth.client.build_http_context') as mock_build_context:
            with patch('keycardai.oauth.client.register_client') as mock_register:
                mock_register.return_value = Mock()

                try:
                    client.register_client(client_name="TestClient")
                except Exception:
                    pass  # We just want to check the context building

                # Verify build_http_context was called with default user agent
                mock_build_context.assert_called()
                call_kwargs = mock_build_context.call_args.kwargs
                assert call_kwargs['user_agent'] == "KeyCardAI-OAuth/0.0.1"

        # Test 3: Token exchange request
        with patch('keycardai.oauth.client.build_http_context') as mock_build_context:
            with patch('keycardai.oauth.client.exchange_token') as mock_exchange:
                mock_exchange.return_value = Mock()

                try:
                    client.exchange_token(subject_token="test_token")
                except Exception:
                    pass  # We just want to check the context building

                # Verify build_http_context was called with default user agent
                mock_build_context.assert_called()
                call_kwargs = mock_build_context.call_args.kwargs
                assert call_kwargs['user_agent'] == "KeyCardAI-OAuth/0.0.1"

    @pytest.mark.asyncio
    async def test_default_user_agent_in_async_requests(self):
        """Test that default user agent is set correctly in all async request types when not configured by user."""
        # Create async client with default config (no custom user_agent)
        async_client = AsyncClient("https://test.keycard.cloud")

        # Test 1: Async server metadata discovery request
        with patch('keycardai.oauth.client.build_http_context') as mock_build_context:
            with patch('keycardai.oauth.client.discover_server_metadata_async') as mock_discover:
                mock_discover.return_value = Mock()

                try:
                    await async_client.discover_server_metadata(base_url="https://test.keycard.cloud")
                except Exception:
                    pass  # We just want to check the context building

                # Verify build_http_context was called with default user agent
                mock_build_context.assert_called()
                call_kwargs = mock_build_context.call_args.kwargs
                assert call_kwargs['user_agent'] == "KeyCardAI-OAuth/0.0.1"

        # Test 2: Async client registration request
        with patch('keycardai.oauth.client.build_http_context') as mock_build_context:
            with patch('keycardai.oauth.client.register_client_async') as mock_register:
                mock_register.return_value = Mock()

                try:
                    await async_client.register_client(client_name="TestClient")
                except Exception:
                    pass  # We just want to check the context building

                # Verify build_http_context was called with default user agent
                mock_build_context.assert_called()
                call_kwargs = mock_build_context.call_args.kwargs
                assert call_kwargs['user_agent'] == "KeyCardAI-OAuth/0.0.1"

        # Test 3: Async token exchange request
        with patch('keycardai.oauth.client.build_http_context') as mock_build_context:
            with patch('keycardai.oauth.client.exchange_token_async') as mock_exchange:
                mock_exchange.return_value = Mock()

                try:
                    await async_client.exchange_token(subject_token="test_token")
                except Exception:
                    pass  # We just want to check the context building

                # Verify build_http_context was called with default user agent
                mock_build_context.assert_called()
                call_kwargs = mock_build_context.call_args.kwargs
                assert call_kwargs['user_agent'] == "KeyCardAI-OAuth/0.0.1"

    def test_custom_user_agent_in_requests(self):
        """Test that custom user agent is used when provided in client config."""
        custom_user_agent = "MyApp/1.2.3"
        config = ClientConfig(
            user_agent=custom_user_agent,
            enable_metadata_discovery=False,
            auto_register_client=False
        )
        client = Client("https://test.keycard.cloud", config=config)

        # Test that custom user agent is used
        with patch('keycardai.oauth.client.build_http_context') as mock_build_context:
            with patch('keycardai.oauth.client.register_client') as mock_register:
                mock_register.return_value = Mock()

                try:
                    client.register_client(client_name="TestClient")
                except Exception:
                    pass  # We just want to check the context building

                # Verify build_http_context was called with custom user agent
                mock_build_context.assert_called()
                call_kwargs = mock_build_context.call_args.kwargs
                assert call_kwargs['user_agent'] == custom_user_agent
