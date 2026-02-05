"""End-to-end tests for OAuth flows.

Tests the complete OAuth flow: discovery -> registration -> token exchange.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from keycardai.mcp.client.auth.oauth.discovery import OAuthDiscoveryService
from keycardai.mcp.client.auth.oauth.exchange import OAuthTokenExchangeService
from keycardai.mcp.client.auth.oauth.registration import OAuthClientRegistrationService
from keycardai.mcp.client.auth.storage_facades import OAuthStorage
from keycardai.mcp.client.storage import InMemoryBackend, NamespacedStorage


class TestOAuthE2EFlow:
    """End-to-end tests for complete OAuth flow."""

    @pytest.fixture
    def oauth_storage(self):
        """Create OAuth storage for testing."""
        backend = InMemoryBackend()
        base_storage = NamespacedStorage(backend, "e2e:test:oauth")
        return OAuthStorage(base_storage)

    @pytest.fixture
    def mock_http_client(self):
        """Create configurable mock HTTP client."""
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        return mock_client

    @pytest.mark.asyncio
    async def test_complete_oauth_flow_discovery_to_token(
        self, oauth_storage, mock_http_client
    ):
        """Test complete flow: discovery -> registration -> token exchange."""
        # Setup: Configure mock responses for each step
        auth_server_url = "https://auth.keycard.test"

        # Step 2: Auth server metadata response
        auth_server_response = MagicMock()
        auth_server_response.status_code = 200
        auth_server_response.raise_for_status = MagicMock()
        auth_server_response.json.return_value = {
            "issuer": auth_server_url,
            "authorization_endpoint": f"{auth_server_url}/authorize",
            "token_endpoint": f"{auth_server_url}/token",
            "registration_endpoint": f"{auth_server_url}/register",
            "jwks_uri": f"{auth_server_url}/.well-known/jwks.json",
        }

        # Step 3: Client registration response
        registration_response = MagicMock()
        registration_response.status_code = 200
        registration_response.raise_for_status = MagicMock()
        registration_response.json.return_value = {
            "client_id": "e2e_test_client",
            "client_secret": None,
            "redirect_uris": ["http://localhost:8080/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "none",
        }

        # Step 4: Token exchange response
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            "access_token": "e2e_access_token_xyz",
            "refresh_token": "e2e_refresh_token_abc",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

        # Configure mock to return different responses based on URL
        call_log = {"get": [], "post": []}

        async def mock_get(url):
            call_log["get"].append(url)
            if ".well-known/oauth-authorization-server" in url:
                return auth_server_response
            raise ValueError(f"Unexpected GET URL: {url}")

        async def mock_post(url, **kwargs):
            call_log["post"].append(url)
            if "/register" in url:
                return registration_response
            elif "/token" in url:
                return token_response
            raise ValueError(f"Unexpected POST URL: {url}")

        mock_http_client.get = mock_get
        mock_http_client.post = mock_post

        def client_factory():
            return mock_http_client

        # Execute: Run the complete flow

        # 1. Auth server discovery (simulating resource metadata already available)
        resource_metadata = {"authorization_servers": [auth_server_url]}

        discovery_service = OAuthDiscoveryService(
            storage=oauth_storage, client_factory=client_factory
        )

        auth_metadata = await discovery_service.discover_auth_server(resource_metadata)
        assert auth_metadata["token_endpoint"] == f"{auth_server_url}/token"
        assert auth_metadata["registration_endpoint"] == f"{auth_server_url}/register"

        # 2. Client registration
        registration_service = OAuthClientRegistrationService(
            storage=oauth_storage, client_name="E2E Test Client", client_factory=client_factory
        )

        client_info = await registration_service.get_or_register_client(
            auth_metadata, ["http://localhost:8080/callback"]
        )
        assert client_info["client_id"] == "e2e_test_client"

        # 3. Token exchange (requires PKCE state)
        state = "e2e_test_state"
        await oauth_storage.save_pkce_state(
            state=state,
            pkce_data={
                "code_verifier": "e2e_verifier",
                "code_challenge": "e2e_challenge",
                "redirect_uri": "http://localhost:8080/callback",
                "resource_url": "https://api.test.com",
            },
            ttl=timedelta(minutes=10),
        )

        exchange_service = OAuthTokenExchangeService(
            storage=oauth_storage, client_factory=client_factory
        )

        tokens = await exchange_service.exchange_code_for_tokens(
            code="e2e_auth_code",
            state=state,
            auth_server_metadata=auth_metadata,
            client_info=client_info,
        )

        # Verify: Complete flow succeeded
        assert tokens["access_token"] == "e2e_access_token_xyz"
        assert tokens["refresh_token"] == "e2e_refresh_token_abc"

        # Verify all steps were called
        assert len(call_log["get"]) >= 1  # Auth server discovery
        assert len(call_log["post"]) >= 2  # Registration + token exchange

    @pytest.mark.asyncio
    async def test_oauth_flow_with_cached_metadata(self, oauth_storage, mock_http_client):
        """Test that cached metadata is reused in subsequent flows."""
        # Pre-cache auth server metadata
        cached_metadata = {
            "issuer": "https://cached.keycard.test",
            "authorization_endpoint": "https://cached.keycard.test/authorize",
            "token_endpoint": "https://cached.keycard.test/token",
            "registration_endpoint": "https://cached.keycard.test/register",
        }
        await oauth_storage.save_auth_server_metadata(cached_metadata)

        # Pre-cache client registration
        cached_client = {
            "client_id": "cached_client_123",
            "redirect_uris": ["http://localhost:8080/callback"],
        }
        await oauth_storage.save_client_registration(cached_client)

        # HTTP client should NOT be called for discovery or registration
        mock_http_client.get = AsyncMock(side_effect=Exception("Should not be called"))
        mock_http_client.post = AsyncMock(side_effect=Exception("Should not be called"))

        def client_factory():
            return mock_http_client

        # Discovery should return cached metadata
        discovery_service = OAuthDiscoveryService(
            storage=oauth_storage, client_factory=client_factory
        )

        metadata = await discovery_service.discover_auth_server(
            {"authorization_servers": ["https://any.server.com"]}
        )

        assert metadata == cached_metadata

        # Registration should return cached client
        registration_service = OAuthClientRegistrationService(
            storage=oauth_storage, client_name="Test", client_factory=client_factory
        )

        client = await registration_service.get_or_register_client(
            cached_metadata, ["http://localhost:8080/callback"]
        )

        assert client == cached_client

    @pytest.mark.asyncio
    async def test_oauth_flow_handles_discovery_failure(self, oauth_storage, mock_http_client):
        """Test graceful handling when discovery fails."""
        mock_http_client.get = AsyncMock(side_effect=Exception("Network error"))

        def client_factory():
            return mock_http_client

        discovery_service = OAuthDiscoveryService(
            storage=oauth_storage, client_factory=client_factory
        )

        with pytest.raises(ValueError, match="Failed to discover"):
            await discovery_service.discover_auth_server(
                {"authorization_servers": ["https://unreachable.server.com"]}
            )

    @pytest.mark.asyncio
    async def test_oauth_flow_handles_registration_failure(self, oauth_storage, mock_http_client):
        """Test graceful handling when client registration fails."""
        # Mock successful metadata response
        metadata_response = MagicMock()
        metadata_response.status_code = 200
        metadata_response.raise_for_status = MagicMock()

        # Mock failed registration response
        registration_response = MagicMock()
        registration_response.status_code = 400
        registration_response.raise_for_status.side_effect = Exception("Registration failed")

        async def mock_get(url):
            return metadata_response

        async def mock_post(url, **kwargs):
            if "/register" in url:
                return registration_response
            raise ValueError(f"Unexpected POST: {url}")

        mock_http_client.get = mock_get
        mock_http_client.post = mock_post

        def client_factory():
            return mock_http_client

        registration_service = OAuthClientRegistrationService(
            storage=oauth_storage, client_name="Test Client", client_factory=client_factory
        )

        auth_metadata = {"registration_endpoint": "https://auth.test/register"}

        with pytest.raises(Exception, match="Registration failed"):
            await registration_service.get_or_register_client(
                auth_metadata, ["http://localhost:8080/callback"]
            )

    @pytest.mark.asyncio
    async def test_oauth_flow_handles_missing_pkce_state(self, oauth_storage, mock_http_client):
        """Test error handling when PKCE state is missing during token exchange."""

        def client_factory():
            return mock_http_client

        exchange_service = OAuthTokenExchangeService(
            storage=oauth_storage, client_factory=client_factory
        )

        auth_metadata = {"token_endpoint": "https://auth.test/token"}
        client_info = {"client_id": "test_client"}

        with pytest.raises(ValueError, match="No PKCE state found"):
            await exchange_service.exchange_code_for_tokens(
                code="some_code",
                state="nonexistent_state",
                auth_server_metadata=auth_metadata,
                client_info=client_info,
            )
