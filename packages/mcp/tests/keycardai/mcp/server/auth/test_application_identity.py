"""Unit tests for ApplicationCredential providers.

This module tests the ApplicationCredential protocol implementations including
ClientSecret, WebIdentity, and EKSWorkloadIdentity.
"""

import asyncio
import json
import os
import tempfile
import time
from base64 import urlsafe_b64encode
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from keycardai.mcp.server.auth._cache import InMemoryTokenCache
from keycardai.mcp.server.auth.application_credentials import (
    ClientSecret,
    EKSWorkloadIdentity,
    WebIdentity,
)
from keycardai.mcp.server.exceptions import (
    ClientSecretConfigurationError,
    EKSWorkloadIdentityConfigurationError,
    EKSWorkloadIdentityRuntimeError,
)
from keycardai.oauth import BasicAuth, ClientConfig, MultiZoneBasicAuth
from keycardai.oauth.types.models import (
    AuthorizationServerMetadata,
    TokenExchangeRequest,
    TokenResponse,
)


@pytest.fixture
def mock_metadata():
    """Fixture providing mock OAuth server metadata."""
    return AuthorizationServerMetadata(
        issuer="https://test.keycard.cloud",
        authorization_endpoint="https://test.keycard.cloud/auth",
        token_endpoint="https://test.keycard.cloud/token",
        jwks_uri="https://test.keycard.cloud/.well-known/jwks.json"
    )


@pytest.fixture
def mock_client(mock_metadata):
    """Fixture providing a mock async OAuth client."""
    client = AsyncMock()

    async def mock_discover_server_metadata():
        return mock_metadata

    client.discover_server_metadata.side_effect = mock_discover_server_metadata

    # Set up _initialized and _discovered_endpoints for audience lookup
    client._initialized = True
    client._discovered_endpoints = AsyncMock()
    client._discovered_endpoints.token = mock_metadata.token_endpoint

    return client


class TestClientSecret:
    """Test ClientSecret for client secret credential-based authentication."""

    @pytest.mark.asyncio
    async def test_initialization_with_tuple(self):
        """Test ClientSecret initialization with credential tuple."""
        provider = ClientSecret(("test_client_id", "test_client_secret"))

        # Should construct BasicAuth internally
        assert isinstance(provider.auth, BasicAuth)
        assert provider.auth.client_id == "test_client_id"
        assert provider.auth.client_secret == "test_client_secret"

    @pytest.mark.asyncio
    async def test_initialization_with_dict(self):
        """Test ClientSecret initialization with credential dict."""
        provider = ClientSecret({
            "zone1": ("client_id_1", "client_secret_1"),
            "zone2": ("client_id_2", "client_secret_2"),
        })

        # Should construct MultiZoneBasicAuth internally
        assert isinstance(provider.auth, MultiZoneBasicAuth)
        assert provider.auth.has_zone("zone1")
        assert provider.auth.has_zone("zone2")

    @pytest.mark.asyncio
    async def test_prepare_token_exchange_request(self, mock_client):
        """Test token exchange request preparation with client secret credentials."""
        provider = ClientSecret(("test_client_id", "test_client_secret"))

        request = await provider.prepare_token_exchange_request(
            client=mock_client,
            subject_token="test_access_token",
            resource="https://api.example.com",
        )

        assert isinstance(request, TokenExchangeRequest)
        assert request.subject_token == "test_access_token"
        assert request.resource == "https://api.example.com"
        assert request.subject_token_type == "urn:ietf:params:oauth:token-type:access_token"
        # Client authentication is handled at HTTP level via AuthStrategy
        assert request.client_assertion is None
        assert request.client_assertion_type is None

    @pytest.mark.asyncio
    async def test_prepare_token_exchange_request_with_auth_info(self, mock_client):
        """Test that auth_info is passed but unused (authentication is via AuthStrategy)."""
        provider = ClientSecret(("test_client_id", "test_client_secret"))

        request = await provider.prepare_token_exchange_request(
            client=mock_client,
            subject_token="test_access_token",
            resource="https://api.example.com",
            auth_info={"zone_id": "zone1", "resource_client_id": "https://mcp.example.com"}
        )

        # Request is prepared successfully
        assert request.subject_token == "test_access_token"
        assert request.resource == "https://api.example.com"
        # Authentication happens at HTTP level, not in the request
        assert request.client_assertion is None

    @pytest.mark.asyncio
    async def test_initialization_with_invalid_type_raises_error(self):
        """Test that ClientSecret raises ClientSecretConfigurationError for invalid types."""
        with pytest.raises(ClientSecretConfigurationError) as exc_info:
            ClientSecret("invalid_string_type")

        assert "Invalid credentials type provided to ClientSecret" in str(exc_info.value)
        assert "str" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_initialization_with_invalid_list_type_raises_error(self):
        """Test that ClientSecret raises ClientSecretConfigurationError for list type."""
        with pytest.raises(ClientSecretConfigurationError) as exc_info:
            ClientSecret(["client_id", "client_secret"])

        assert "Invalid credentials type provided to ClientSecret" in str(exc_info.value)
        assert "list" in str(exc_info.value)


class TestWebIdentity:
    """Test WebIdentity for private key JWT authentication."""

    @pytest.mark.asyncio
    async def test_initialization(self):
        """Test WebIdentity initialization creates keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir
            )

            # Verify keys were created
            key_dir = Path(tmpdir)
            pem_files = list(key_dir.glob("*.pem"))
            json_files = list(key_dir.glob("*.json"))

            assert len(pem_files) == 1
            assert len(json_files) == 1

            # Verify JWKS is available
            jwks = provider.get_jwks()
            assert jwks is not None
            assert len(jwks.keys) == 1

    @pytest.mark.asyncio
    async def test_prepare_token_exchange_request(self, mock_client):
        """Test JWT client assertion generation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir
            )

            request = await provider.prepare_token_exchange_request(
                client=mock_client,
                subject_token="test_access_token",
                resource="https://api.example.com",
                auth_info={"resource_client_id": "https://mcp.example.com"}
            )

            assert isinstance(request, TokenExchangeRequest)
            assert request.subject_token == "test_access_token"
            assert request.resource == "https://api.example.com"
            assert request.subject_token_type == "urn:ietf:params:oauth:token-type:access_token"
            assert request.client_assertion is not None
            assert request.client_assertion_type == "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

            # Verify JWT structure (should have 3 parts separated by dots)
            jwt_parts = request.client_assertion.split(".")
            assert len(jwt_parts) == 3

    @pytest.mark.asyncio
    async def test_prepare_token_exchange_request_without_auth_info(self, mock_client):
        """Test that missing auth_info raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir
            )

            with pytest.raises(ValueError, match="auth_info with 'resource_client_id' is required"):
                await provider.prepare_token_exchange_request(
                    client=mock_client,
                    subject_token="test_access_token",
                    resource="https://api.example.com",
                )

    @pytest.mark.asyncio
    async def test_key_persistence(self):
        """Test that keys persist across provider instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create first provider
            provider1 = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir,
                key_id="stable-key-id"
            )
            jwks1 = provider1.get_jwks()

            # Create second provider with same storage
            provider2 = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir,
                key_id="stable-key-id"
            )
            jwks2 = provider2.get_jwks()

            # Should have the same public keys
            assert jwks1.keys[0].kid == jwks2.keys[0].kid
            assert jwks1.keys[0].n == jwks2.keys[0].n
            assert jwks1.keys[0].e == jwks2.keys[0].e

    @pytest.mark.asyncio
    async def test_custom_key_id(self):
        """Test WebIdentity with custom key ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir,
                key_id="custom-stable-id"
            )

            jwks = provider.get_jwks()
            assert jwks.keys[0].kid == "custom-stable-id"

    @pytest.mark.asyncio
    async def test_audience_config(self, mock_client):
        """Test WebIdentity with audience configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = WebIdentity(
                mcp_server_name="Test Server",
                storage_dir=tmpdir,
                audience_config="https://custom-audience.example.com"
            )

            request = await provider.prepare_token_exchange_request(
                client=mock_client,
                subject_token="test_access_token",
                resource="https://api.example.com",
                auth_info={"resource_client_id": "https://mcp.example.com"}
            )

            # JWT should be created successfully
            assert request.client_assertion is not None


class TestEKSWorkloadIdentity:
    """Test EKSWorkloadIdentity for EKS workload identity tokens."""

    @pytest.mark.asyncio
    async def test_initialization_with_token_file_path(self):
        """Test EKSWorkloadIdentity initialization with explicit token file path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            token_file.write_text("eks-test-token-12345")

            provider = EKSWorkloadIdentity(token_file_path=str(token_file))

            assert provider.token_file_path == str(token_file)

    @pytest.mark.asyncio
    async def test_initialization_with_env_var(self):
        """Test EKSWorkloadIdentity initialization with environment variable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            token_file.write_text("eks-test-token-12345")

            # Set environment variable
            os.environ["AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE"] = str(token_file)
            try:
                provider = EKSWorkloadIdentity()
                assert provider.token_file_path == str(token_file)
            finally:
                os.environ.pop("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE", None)

    @pytest.mark.asyncio
    async def test_initialization_with_custom_env_var(self):
        """Test EKSWorkloadIdentity initialization with custom environment variable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            token_file.write_text("eks-test-token-12345")

            # Set custom environment variable
            os.environ["CUSTOM_TOKEN_FILE"] = str(token_file)
            try:
                provider = EKSWorkloadIdentity(env_var_name="CUSTOM_TOKEN_FILE")
                assert provider.token_file_path == str(token_file)
                assert provider.env_var_name == "CUSTOM_TOKEN_FILE"
            finally:
                os.environ.pop("CUSTOM_TOKEN_FILE", None)

    @pytest.mark.asyncio
    async def test_initialization_fails_when_token_file_not_found(self):
        """Test that initialization fails when token file doesn't exist."""
        with pytest.raises(EKSWorkloadIdentityConfigurationError) as exc_info:
            EKSWorkloadIdentity(token_file_path="/nonexistent/token/path")

        assert "Failed to initialize EKS workload identity" in str(exc_info.value)
        assert "/nonexistent/token/path" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_initialization_fails_when_env_var_not_set(self):
        """Test that initialization fails when environment variable is not set."""
        # Ensure the env var is not set
        os.environ.pop("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE", None)

        with pytest.raises(EKSWorkloadIdentityConfigurationError) as exc_info:
            EKSWorkloadIdentity()

        assert "Failed to initialize EKS workload identity" in str(exc_info.value)
        assert "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_initialization_fails_when_token_file_empty(self):
        """Test that initialization fails when token file is empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            token_file.write_text("")  # Empty file

            with pytest.raises(EKSWorkloadIdentityConfigurationError) as exc_info:
                EKSWorkloadIdentity(token_file_path=str(token_file))

            assert "Failed to initialize EKS workload identity" in str(exc_info.value)
            assert "Token file is empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_prepare_token_exchange_request(self, mock_client):
        """Test token exchange request preparation with EKS workload identity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            test_token = "eks-test-token-12345"
            token_file.write_text(test_token)

            provider = EKSWorkloadIdentity(token_file_path=str(token_file))
            provider.get_application_credential = AsyncMock(return_value="eks-test-token-12345")

            request = await provider.prepare_token_exchange_request(
                client=mock_client,
                subject_token="test_access_token",
                resource="https://api.example.com",
            )

            assert isinstance(request, TokenExchangeRequest)
            assert request.subject_token == "test_access_token"
            assert request.resource == "https://api.example.com"
            assert request.subject_token_type == "urn:ietf:params:oauth:token-type:access_token"
            assert request.client_assertion == test_token
            assert request.client_assertion_type == "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

    @pytest.mark.asyncio
    async def test_prepare_token_exchange_request_with_auth_info(self, mock_client):
        """Test that auth_info is ignored for EKSWorkloadIdentity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            test_token = "eks-test-token-12345"
            token_file.write_text(test_token)

            provider = EKSWorkloadIdentity(token_file_path=str(token_file))
            provider.get_application_credential = AsyncMock(return_value="eks-test-token-12345")

            request = await provider.prepare_token_exchange_request(
                client=mock_client,
                subject_token="test_access_token",
                resource="https://api.example.com",
                auth_info={"resource_client_id": "https://mcp.example.com"}
            )

            # Should work fine even with auth_info provided
            assert request.subject_token == "test_access_token"
            assert request.client_assertion == test_token

    @pytest.mark.asyncio
    async def test_token_read_on_each_request(self, mock_client):
        """Test that token is read fresh on each request (not cached)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"

            # Write initial token
            token_file.write_text("token-v1")
            provider = EKSWorkloadIdentity(token_file_path=str(token_file))
            provider.get_application_credential = AsyncMock(return_value="token-v1")
            # First request
            request1 = await provider.prepare_token_exchange_request(
                client=mock_client,
                subject_token="test_access_token",
                resource="https://api.example.com",
            )
            assert request1.client_assertion == "token-v1"

            # Update token file
            token_file.write_text("token-v2")
            provider.get_application_credential = AsyncMock(return_value="token-v2")
            # Second request should read the new token
            request2 = await provider.prepare_token_exchange_request(
                client=mock_client,
                subject_token="test_access_token",
                resource="https://api.example.com",
            )
            assert request2.client_assertion == "token-v2"

    @pytest.mark.asyncio
    async def test_token_whitespace_is_stripped(self, mock_client):
        """Test that whitespace is stripped from token."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            token_file.write_text("  token-with-whitespace  \n")

            provider = EKSWorkloadIdentity(token_file_path=str(token_file))
            provider.get_application_credential = AsyncMock(return_value="token-with-whitespace")

            request = await provider.prepare_token_exchange_request(
                client=mock_client,
                subject_token="test_access_token",
                resource="https://api.example.com",
            )

            assert request.client_assertion == "token-with-whitespace"

    @pytest.mark.asyncio
    async def test_set_client_config_returns_unmodified_config(self, mock_client):
        """Test that set_client_config returns unmodified config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            token_file.write_text("test-token")

            provider = EKSWorkloadIdentity(token_file_path=str(token_file))

            config = ClientConfig()
            auth_info = {"resource_client_id": "test-client"}

            result = provider.set_client_config(config, auth_info)

            # Should return the same config object unchanged
            assert result is config

    @pytest.mark.asyncio
    async def test_runtime_error_when_token_deleted_after_init(self, mock_client):
        """Test that runtime error is raised when token is deleted after initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            token_file.write_text("test-token")

            # Initialize successfully
            provider = EKSWorkloadIdentity(token_file_path=str(token_file))

            # Delete the token file after initialization
            token_file.unlink()

            # Should raise runtime error, not configuration error
            with pytest.raises(EKSWorkloadIdentityRuntimeError) as exc_info:
                await provider.prepare_token_exchange_request(
                    client=mock_client,
                    subject_token="test_access_token",
                    resource="https://api.example.com",
                )

            assert "Failed to read EKS workload identity token at runtime" in str(exc_info.value)
            assert "Token file not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_runtime_error_when_token_becomes_empty(self, mock_client):
        """Test that runtime error is raised when token file becomes empty after init."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"
            token_file.write_text("test-token")

            # Initialize successfully
            provider = EKSWorkloadIdentity(token_file_path=str(token_file))

            # Empty the token file after initialization
            token_file.write_text("")

            # Should raise runtime error for empty token
            with pytest.raises(EKSWorkloadIdentityRuntimeError) as exc_info:
                await provider.prepare_token_exchange_request(
                    client=mock_client,
                    subject_token="test_access_token",
                    resource="https://api.example.com",
                )

            assert "Failed to read EKS workload identity token at runtime" in str(exc_info.value)
            assert "Token file is empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_cached_token_expiration_triggers_refresh(self, mock_client):
        """Test that expired cached tokens trigger backend refresh.

        This test verifies:
        1. A token is cached on first request
        2. When the cached token expires (based on JWT exp claim), it's removed from cache
        3. A new token is fetched from the backend
        4. The new token is cached
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"

            # Create mock EKS token (client assertion) with jti claim
            current_time = int(time.time())
            eks_token_payload = {
                "iss": "https://eks.amazonaws.com",
                "sub": "system:serviceaccount:default:my-service",
                "aud": "https://api.keycard.sh",
                "iat": current_time,
                "exp": current_time + 3600,
                "jti": "eks-token-jti-12345"  # Used as cache key
            }
            eks_token = create_mock_jwt(eks_token_payload)
            token_file.write_text(eks_token)

            # Create custom cache with 5 minute leeway
            cache = InMemoryTokenCache(exp_leeway=300)
            provider = EKSWorkloadIdentity(
                token_file_path=str(token_file),
                cache=cache
            )

            # Mock the OAuth client's exchange_token response
            # First response: token that will be valid initially but expire after time passes
            initial_token_payload = {
                "iss": "http://test.keycard.sh",
                "aud": "https://api.keycard.sh",
                "sub": "019a02b8-a7ad-79d0-86f9-d17e08a55ef5",
                "iat": current_time,
                "exp": current_time + 400  # Expires in 400 seconds (outside 300s leeway initially)
            }
            initial_access_token = create_mock_jwt(initial_token_payload)

            # Second response: fresh token with long expiration
            fresh_token_payload = {
                "iss": "http://test.keycard.sh",
                "aud": "https://api.keycard.sh",
                "sub": "019a02b8-a7ad-79d0-86f9-d17e08a55ef5",
                "iat": current_time + 200,
                "exp": current_time + 3800  # Expires in ~1 hour (outside leeway)
            }
            fresh_access_token = create_mock_jwt(fresh_token_payload)

            # First call: return token that's valid now
            mock_client.exchange_token = AsyncMock(
                return_value=TokenResponse(
                    access_token=initial_access_token,
                    token_type="Bearer",
                    expires_in=400
                )
            )

            # FIRST REQUEST: Should call backend and cache the token
            result1 = await provider.get_application_credential(mock_client, eks_token)
            assert result1 == initial_access_token
            assert mock_client.exchange_token.call_count == 1

            # Verify token is cached
            cached = cache.get("eks-token-jti-12345")
            assert cached is not None
            assert cached[0] == initial_access_token

            # SECOND REQUEST: Token is still valid (within cache), should return cached
            result2 = await provider.get_application_credential(mock_client, eks_token)
            assert result2 == initial_access_token
            assert mock_client.exchange_token.call_count == 1  # No new call

            # SIMULATE TIME PASSING: Move time forward by 150 seconds
            # Token expires at current_time + 400
            # After 150 seconds: time is current_time + 150
            # Time until expiration: 400 - 150 = 250 seconds
            # Since 250 < 300 (leeway), token should be considered expired
            with patch('time.time', return_value=current_time + 150):
                # Now the cached token should be considered expired
                cached_after_time_pass = cache.get("eks-token-jti-12345")
                assert cached_after_time_pass is None  # Cache should return None for expired token

                # Update mock to return fresh token
                mock_client.exchange_token = AsyncMock(
                    return_value=TokenResponse(
                        access_token=fresh_access_token,
                        token_type="Bearer",
                        expires_in=3600
                    )
                )

                # THIRD REQUEST: Should detect expiration and fetch new token
                result3 = await provider.get_application_credential(mock_client, eks_token)
                assert result3 == fresh_access_token
                assert mock_client.exchange_token.call_count == 1  # New call made

                # Verify new token is cached
                cached_fresh = cache.get("eks-token-jti-12345")
                assert cached_fresh is not None
                assert cached_fresh[0] == fresh_access_token

    @pytest.mark.asyncio
    async def test_concurrent_requests_only_one_backend_call(self, mock_client):
        """Test that concurrent requests only result in one backend call (double-checked locking).

        This verifies that the double-checked locking pattern prevents thundering herd:
        - Multiple coroutines request the same credential simultaneously
        - Only ONE makes the backend call
        - Others wait and get the cached result
        """

        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "token"

            # Create mock EKS token
            current_time = int(time.time())
            eks_token_payload = {
                "iss": "https://eks.amazonaws.com",
                "sub": "system:serviceaccount:default:my-service",
                "aud": "https://api.keycard.sh",
                "iat": current_time,
                "exp": current_time + 3600,
                "jti": "concurrent-test-jti"
            }
            eks_token = create_mock_jwt(eks_token_payload)
            token_file.write_text(eks_token)

            cache = InMemoryTokenCache(exp_leeway=300)
            provider = EKSWorkloadIdentity(
                token_file_path=str(token_file),
                cache=cache
            )

            # Create access token response
            access_token_payload = {
                "iss": "http://test.keycard.sh",
                "aud": "https://api.keycard.sh",
                "sub": "019a02b8-a7ad-79d0-86f9-d17e08a55ef5",
                "iat": current_time,
                "exp": current_time + 3600
            }
            access_token = create_mock_jwt(access_token_payload)

            # Add small delay to exchange_token to simulate network latency
            async def mock_exchange_with_delay(request):
                await asyncio.sleep(0.1)  # 100ms delay
                return TokenResponse(
                    access_token=access_token,
                    token_type="Bearer",
                    expires_in=3600
                )

            mock_client.exchange_token = AsyncMock(side_effect=mock_exchange_with_delay)

            # Launch 10 concurrent requests
            tasks = [
                provider.get_application_credential(mock_client, eks_token)
                for _ in range(10)
            ]
            results = await asyncio.gather(*tasks)

            # All should return the same token
            assert all(r == access_token for r in results)

            # But only ONE backend call should have been made (double-checked locking)
            assert mock_client.exchange_token.call_count == 1


def create_mock_jwt(payload: dict) -> str:
    """Create a mock JWT token for testing.

    Note: This creates an UNSIGNED JWT (algorithm: none) for testing purposes.
    The signature verification is disabled in the code when decoding these tokens.

    Args:
        payload: JWT payload claims

    Returns:
        JWT token string in format: header.payload.signature
    """
    # Create header with "none" algorithm (unsigned)
    header = {"alg": "none", "typ": "JWT"}

    # Encode header and payload
    def b64_encode(data: dict) -> str:
        json_bytes = json.dumps(data, separators=(',', ':')).encode('utf-8')
        return urlsafe_b64encode(json_bytes).rstrip(b'=').decode('utf-8')

    encoded_header = b64_encode(header)
    encoded_payload = b64_encode(payload)

    # For "none" algorithm, signature is empty
    return f"{encoded_header}.{encoded_payload}."

