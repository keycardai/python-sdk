"""Unit tests for Dynamic Client Registration (RFC 7591) functionality."""

from unittest.mock import AsyncMock, Mock

import pytest

from keycardai.oauth import (
    AsyncClient,
    Client,
    ClientCredentialsAuth,
    ClientRegistrationResponse,
    GrantType,
    ResponseType,
    TokenEndpointAuthMethod,
)
from keycardai.oauth.client.http import HTTPClient, HTTPClientProtocol
from keycardai.oauth.client.operations import register_client, register_client_async
from keycardai.oauth.exceptions import ConfigError


class TestClientRegistrationModels:
    """Test the ClientRegistrationResponse model parsing and normalization."""

    def test_client_registration_response_from_response_minimal(self):
        """Test parsing minimal DCR response with only required fields."""
        response_data = {
            "client_id": "test_client_123",
            "client_secret": "test_secret_456",
        }

        response = ClientRegistrationResponse.from_response(response_data)

        assert response.client_id == "test_client_123"
        assert response.client_secret == "test_secret_456"
        assert response.client_name is None
        assert response.raw == response_data

    def test_client_registration_response_from_response_complete(self):
        """Test parsing complete DCR response with all RFC 7591 fields."""
        response_data = {
            "client_id": "test_client_123",
            "client_secret": "test_secret_456",
            "client_id_issued_at": 1640995200,
            "client_secret_expires_at": 1672531200,
            "client_name": "Test Service",
            "jwks_uri": "https://service.example.com/.well-known/jwks.json",
            "token_endpoint_auth_method": "private_key_jwt",
            "redirect_uris": ["https://app.example.com/callback"],
            "grant_types": ["client_credentials", "authorization_code"],
            "response_types": ["code"],
            "scope": "read write admin",
            "registration_access_token": "reg_access_token_123",
            "registration_client_uri": "https://auth.example.com/register/test_client_123",
        }
        headers = {"Content-Type": "application/json"}

        response = ClientRegistrationResponse.from_response(response_data, headers)

        assert response.client_id == "test_client_123"
        assert response.client_secret == "test_secret_456"
        assert response.client_name == "Test Service"
        assert response.jwks_uri == "https://service.example.com/.well-known/jwks.json"
        assert response.scope == ["read", "write", "admin"]  # Normalized from string
        assert response.redirect_uris == ["https://app.example.com/callback"]
        assert response.grant_types == ["client_credentials", "authorization_code"]
        assert response.response_types == ["code"]
        assert response.raw == response_data
        assert response.headers == headers

    def test_client_registration_response_scope_normalization(self):
        """Test scope parameter normalization from string to list."""
        # Test space-delimited string
        response_data = {"client_id": "test", "scope": "read write admin"}
        response = ClientRegistrationResponse.from_response(response_data)
        assert response.scope == ["read", "write", "admin"]

        # Test empty string
        response_data = {"client_id": "test", "scope": ""}
        response = ClientRegistrationResponse.from_response(response_data)
        assert response.scope is None

        # Test list format (should preserve)
        response_data = {"client_id": "test", "scope": ["read", "write"]}
        response = ClientRegistrationResponse.from_response(response_data)
        assert response.scope == ["read", "write"]

    def test_client_registration_response_array_normalization(self):
        """Test array field normalization from strings to lists."""
        response_data = {
            "client_id": "test",
            "redirect_uris": "https://app.example.com/callback",
            "grant_types": "client_credentials",
            "response_types": "code",
        }

        response = ClientRegistrationResponse.from_response(response_data)

        assert response.redirect_uris == ["https://app.example.com/callback"]
        assert response.grant_types == ["client_credentials"]
        assert response.response_types == ["code"]


class TestRegisterClientOperation:
    """Test the register_client operation function directly."""

    @pytest.mark.asyncio
    async def test_register_client_minimal_s2s(self):
        """Test minimal service-to-service client registration."""
        mock_http_client = AsyncMock(spec=HTTPClientProtocol)
        mock_auth_strategy = Mock()
        mock_auth_strategy.authenticate = AsyncMock(
            return_value={"Authorization": "Bearer token"}
        )
        mock_auth_strategy.get_basic_auth.return_value = None

        response_data = {
            "client_id": "generated_client_123",
            "client_secret": "generated_secret_456",
            "client_name": "FastMCP Service",
            "jwks_uri": "https://service.example.com/.well-known/jwks.json",
            "token_endpoint_auth_method": "private_key_jwt",
        }
        mock_http_client.request.return_value = response_data

        response = await register_client_async(
            client_name="FastMCP Service",
            registration_endpoint="https://auth.example.com/oauth2/register",
            auth_strategy=mock_auth_strategy,
            http_client=mock_http_client,
            jwks_uri="https://service.example.com/.well-known/jwks.json",
            token_endpoint_auth_method=TokenEndpointAuthMethod.PRIVATE_KEY_JWT,
        )

        # Verify HTTP request was made
        mock_http_client.request.assert_called_once()
        call_args = mock_http_client.request.call_args

        # Verify request parameters
        assert call_args[1]["method"] == "POST"
        assert call_args[1]["url"] == "https://auth.example.com/oauth2/register"
        assert call_args[1]["json"]["client_name"] == "FastMCP Service"
        assert (
            call_args[1]["json"]["jwks_uri"]
            == "https://service.example.com/.well-known/jwks.json"
        )
        assert call_args[1]["json"]["token_endpoint_auth_method"] == "private_key_jwt"
        assert "Content-Type" in call_args[1]["headers"]
        assert "Authorization" in call_args[1]["headers"]

        # Verify response parsing
        assert isinstance(response, ClientRegistrationResponse)
        assert response.client_id == "generated_client_123"
        assert response.client_name == "FastMCP Service"

    @pytest.mark.asyncio
    async def test_register_client_web_app_full(self):
        """Test full web application client registration with all parameters."""
        mock_http_client = AsyncMock(spec=HTTPClientProtocol)
        mock_auth_strategy = Mock()
        mock_auth_strategy.authenticate = AsyncMock(return_value={})
        mock_auth_strategy.get_basic_auth.return_value = ("client", "secret")

        response_data = {
            "client_id": "web_app_123",
            "client_secret": "web_secret_456",
            "client_name": "Customer Portal",
            "redirect_uris": [
                "https://portal.com/callback",
                "https://portal.com/silent",
            ],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": "openid profile email customer:read",
            "token_endpoint_auth_method": "client_secret_post",
        }
        mock_http_client.request.return_value = response_data

        response = await register_client_async(
            client_name="Customer Portal",
            registration_endpoint="https://auth.example.com/oauth2/register",
            auth_strategy=mock_auth_strategy,
            http_client=mock_http_client,
            redirect_uris=["https://portal.com/callback", "https://portal.com/silent"],
            grant_types=[GrantType.AUTHORIZATION_CODE, GrantType.REFRESH_TOKEN],
            response_types=[ResponseType.CODE],
            scope=["openid", "profile", "email", "customer:read"],
            token_endpoint_auth_method=TokenEndpointAuthMethod.CLIENT_SECRET_POST,
            # Vendor extensions
            client_uri="https://portal.com",
            logo_uri="https://portal.com/logo.png",
        )

        # Verify request includes all parameters
        mock_http_client.request.assert_called_once()
        call_args = mock_http_client.request.call_args

        # Verify basic call structure
        assert call_args[1]["method"] == "POST"
        assert call_args[1]["url"] == "https://auth.example.com/oauth2/register"
        assert call_args[1]["auth"] == ("client", "secret")

        # Verify JSON payload contains expected fields
        json_data = call_args[1]["json"]
        assert json_data["client_name"] == "Customer Portal"
        assert json_data["redirect_uris"] == [
            "https://portal.com/callback",
            "https://portal.com/silent",
        ]
        assert json_data["grant_types"] == ["authorization_code", "refresh_token"]
        assert json_data["response_types"] == ["code"]
        assert (
            json_data["scope"] == "openid profile email customer:read"
        )  # Converted from list to string
        assert json_data["token_endpoint_auth_method"] == "client_secret_post"
        assert json_data["client_uri"] == "https://portal.com"
        assert json_data["logo_uri"] == "https://portal.com/logo.png"

        assert response.client_id == "web_app_123"
        assert response.scope == ["openid", "profile", "email", "customer:read"]

    @pytest.mark.asyncio
    async def test_register_client_validation_errors(self):
        """Test validation errors for invalid registration requests."""
        mock_http_client = AsyncMock(spec=HTTPClientProtocol)
        mock_auth_strategy = Mock()

        # Test missing registration endpoint
        with pytest.raises(
            ConfigError, match="Client registration endpoint not configured"
        ):
            await register_client_async(
                client_name="Test Service",
                registration_endpoint="",
                auth_strategy=mock_auth_strategy,
                http_client=mock_http_client,
            )

        # Test missing client_name
        with pytest.raises(ValueError, match="client_name is required"):
            await register_client_async(
                client_name="",
                registration_endpoint="https://auth.example.com/oauth2/register",
                auth_strategy=mock_auth_strategy,
                http_client=mock_http_client,
            )

        # Test private_key_jwt without jwks_uri or jwks
        with pytest.raises(
            ValueError,
            match="jwks_uri or jwks is required for TokenEndpointAuthMethod.PRIVATE_KEY_JWT",
        ):
            await register_client_async(
                client_name="Test Service",
                registration_endpoint="https://auth.example.com/oauth2/register",
                auth_strategy=mock_auth_strategy,
                http_client=mock_http_client,
                token_endpoint_auth_method=TokenEndpointAuthMethod.PRIVATE_KEY_JWT,
            )

    def test_register_client_sync_minimal(self):
        """Test synchronous client registration."""
        mock_http_client = Mock(spec=HTTPClient)
        mock_auth_strategy = Mock()
        mock_auth_strategy.get_basic_auth.return_value = ("client", "secret")

        response_data = {
            "client_id": "sync_client_123",
            "client_secret": "sync_secret_456",
            "client_name": "Sync Service",
        }
        mock_http_client.request.return_value = response_data

        response = register_client(
            client_name="Sync Service",
            registration_endpoint="https://auth.example.com/oauth2/register",
            auth_strategy=mock_auth_strategy,
            http_client=mock_http_client,
        )

        # Verify HTTP request
        mock_http_client.request.assert_called_once_with(
            method="POST",
            url="https://auth.example.com/oauth2/register",
            json={
                "client_name": "Sync Service",
                "token_endpoint_auth_method": "client_secret_basic",
            },
            headers={"Content-Type": "application/json"},
            auth=("client", "secret"),
            timeout=None,
        )

        assert isinstance(response, ClientRegistrationResponse)
        assert response.client_id == "sync_client_123"


class TestAsyncClientRegistration:
    """Test Dynamic Client Registration through AsyncClient interface."""

    @pytest.mark.asyncio
    async def test_async_client_register_client_fastmcp_scenario(self):
        """Test AsyncClient.register_client for FastMCP use case."""
        # Mock HTTP client
        mock_http_client = AsyncMock(spec=HTTPClientProtocol)
        response_data = {
            "client_id": "fastmcp_service_123",
            "token_endpoint_auth_method": "private_key_jwt",
            "client_name": "DataProcessingService",
            "jwks_uri": "https://dataservice.com/.well-known/jwks.json",
            "grant_types": [
                "client_credentials",
                "urn:ietf:params:oauth:grant-type:token-exchange",
            ],
            "scope": "data:read data:process delegation:user",
        }
        mock_http_client.request.return_value = response_data

        # Create client with mock
        client = AsyncClient(
            "https://api.keycard.ai",
            auth=ClientCredentialsAuth("initial_client", "initial_secret"),
            http_client=mock_http_client,
        )

        # Register new service
        response = await client.register_client(
            client_name="DataProcessingService",
            jwks_uri="https://dataservice.com/.well-known/jwks.json",
            token_endpoint_auth_method=TokenEndpointAuthMethod.PRIVATE_KEY_JWT,
            grant_types=[
                GrantType.CLIENT_CREDENTIALS,
                GrantType.TOKEN_EXCHANGE,
            ],
            scope=["data:read", "data:process", "delegation:user"],
        )

        # Verify the registration request
        mock_http_client.request.assert_called_once()
        call_args = mock_http_client.request.call_args

        assert call_args[1]["method"] == "POST"
        assert call_args[1]["url"] == "https://api.keycard.ai/oauth2/register"
        assert call_args[1]["json"]["client_name"] == "DataProcessingService"
        assert call_args[1]["json"]["scope"] == "data:read data:process delegation:user"

        # Verify the response
        assert response.client_id == "fastmcp_service_123"
        assert (
            response.token_endpoint_auth_method
            == TokenEndpointAuthMethod.PRIVATE_KEY_JWT
        )

    @pytest.mark.asyncio
    async def test_async_client_register_client_missing_endpoint(self):
        """Test error when registration endpoint is not configured."""
        # Create client without registration endpoint
        client = AsyncClient(
            "https://auth.example.com",
            auth=ClientCredentialsAuth("test_client", "test_secret"),
        )

        # Override to simulate missing endpoint
        client._endpoints.register = None

        with pytest.raises(
            ConfigError, match="Client registration endpoint not configured"
        ):
            await client.register_client(client_name="Test Service")


class TestSyncClientRegistration:
    """Test Dynamic Client Registration through sync Client interface."""

    def test_sync_client_register_client_enterprise_scenario(self):
        """Test Client.register_client for enterprise multi-environment scenario."""
        # Mock HTTP client
        mock_http_client = Mock(spec=HTTPClient)
        response_data = {
            "client_id": "enterprise_service_prod_123",
            "client_secret": "enterprise_secret_456",
            "client_name": "DataService-PROD",
            "jwks_uri": "https://dataservice-prod.company.com/.well-known/jwks.json",
            "token_endpoint_auth_method": "private_key_jwt",
            "grant_types": [
                "client_credentials",
                "urn:ietf:params:oauth:grant-type:token-exchange",
            ],
            "scope": "data:read data:write delegation:user",
        }
        mock_http_client.request.return_value = response_data

        # Create client with mock
        client = Client(
            "https://auth.company.com",
            auth=ClientCredentialsAuth("admin_client", "admin_secret"),
            http_client=mock_http_client,
        )

        # Register enterprise service
        response = client.register_client(
            client_name="DataService-PROD",
            jwks_uri="https://dataservice-prod.company.com/.well-known/jwks.json",
            token_endpoint_auth_method=TokenEndpointAuthMethod.PRIVATE_KEY_JWT,
            grant_types=[
                GrantType.CLIENT_CREDENTIALS,
                GrantType.TOKEN_EXCHANGE,
            ],
            scope="data:read data:write delegation:user",
            # Vendor extensions for enterprise
            environment="prod",
            deployment_id="dataservice-prod-v1.2.3",
            contact_email="ops-prod@company.com",
        )

        # Verify the registration request includes vendor extensions
        mock_http_client.request.assert_called_once()
        call_args = mock_http_client.request.call_args

        request_data = call_args[1]["json"]
        assert request_data["client_name"] == "DataService-PROD"
        assert request_data["environment"] == "prod"
        assert request_data["deployment_id"] == "dataservice-prod-v1.2.3"
        assert request_data["contact_email"] == "ops-prod@company.com"

        # Verify the response
        assert response.client_id == "enterprise_service_prod_123"
        assert response.grant_types == [
            "client_credentials",
            "urn:ietf:params:oauth:grant-type:token-exchange",
        ]


class TestClientRegistrationIntegrationScenarios:
    """Test real-world DCR integration scenarios."""

    @pytest.mark.asyncio
    async def test_fastmcp_zero_config_registration(self):
        """Test FastMCP AuthProvider zero-config service registration scenario."""
        mock_http_client = AsyncMock(spec=HTTPClientProtocol)

        # Simulate KeyCard DCR response for FastMCP service
        dcr_response = {
            "client_id": "fastmcp_auto_generated_123",
            "token_endpoint_auth_method": "private_key_jwt",
            "client_name": "my-fastmcp-service",
            "jwks_uri": "https://my-service.example.com/.well-known/jwks.json",
            "grant_types": ["client_credentials"],
            "scope": "mcp:read mcp:write token:introspect",
            "client_id_issued_at": 1640995200,
            "registration_access_token": "reg_token_123",
            "raw_extensions": {"zone": "production", "service_type": "fastmcp"},
        }
        mock_http_client.request.return_value = dcr_response

        # Create client for auto-discovery and registration
        # Note: Even for DCR, we need bootstrap credentials for the registration request itself
        client = AsyncClient(
            "https://api.keycard.ai",
            auth=ClientCredentialsAuth("bootstrap_client", "bootstrap_secret"),
            http_client=mock_http_client,
        )

        # Simulate FastMCP AuthProvider registration
        registration = await client.register_client(
            client_name="my-fastmcp-service",
            jwks_uri="https://my-service.example.com/.well-known/jwks.json",
            token_endpoint_auth_method=TokenEndpointAuthMethod.PRIVATE_KEY_JWT,
            grant_types=[GrantType.CLIENT_CREDENTIALS],
            scope=["mcp:read", "mcp:write", "token:introspect"],
            # FastMCP-specific metadata
            zone="production",
            service_type="fastmcp",
        )

        # Verify registration enables subsequent operations
        assert registration.client_id == "fastmcp_auto_generated_123"
        assert (
            registration.token_endpoint_auth_method
            == TokenEndpointAuthMethod.PRIVATE_KEY_JWT
        )
        assert "mcp:read" in registration.scope

        # Verify request included FastMCP metadata
        call_args = mock_http_client.request.call_args
        request_data = call_args[1]["json"]
        assert request_data["zone"] == "production"
        assert request_data["service_type"] == "fastmcp"

    def test_enterprise_multi_environment_registration(self):
        """Test enterprise deployment across multiple environments."""
        environments = ["dev", "staging", "prod"]
        registrations = []

        for env in environments:
            mock_http_client = Mock(spec=HTTPClient)
            response_data = {
                "client_id": f"dataservice_{env}_123",
                "client_secret": f"{env}_secret_456",
                "client_name": f"DataService-{env.upper()}",
                "environment": env,
                "deployment_id": f"dataservice-{env}-v1.2.3",
            }
            mock_http_client.request.return_value = response_data

            client = Client(
                f"https://auth-{env}.company.com",
                auth=ClientCredentialsAuth("deploy_client", "deploy_secret"),
                http_client=mock_http_client,
            )

            registration = client.register_client(
                client_name=f"DataService-{env.upper()}",
                jwks_uri=f"https://dataservice-{env}.company.com/.well-known/jwks.json",
                token_endpoint_auth_method=TokenEndpointAuthMethod.PRIVATE_KEY_JWT,
                grant_types=[
                    GrantType.CLIENT_CREDENTIALS,
                    GrantType.TOKEN_EXCHANGE,
                ],
                scope="data:read data:write delegation:user",
                environment=env,
                deployment_id=f"dataservice-{env}-v1.2.3",
                contact_email=f"ops-{env}@company.com",
            )

            registrations.append(
                {
                    "environment": env,
                    "client_id": registration.client_id,
                    "deployment_id": registration.raw.get("deployment_id"),
                }
            )

        # Verify all environments registered successfully
        assert len(registrations) == 3
        assert all(reg["client_id"].startswith("dataservice_") for reg in registrations)
        assert registrations[0]["environment"] == "dev"
        assert registrations[2]["environment"] == "prod"
