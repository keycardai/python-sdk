"""Unit tests for OAuth flow initiator service.

This module tests the OAuthFlowInitiatorService class, focusing on:
- Initiating OAuth flows with PKCE
- Generating and storing PKCE state
- Building authorization URLs
- Handling optional scopes
- Error handling for missing required fields
"""

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from keycardai.mcp.client.auth.oauth.flow import FlowMetadata, OAuthFlowInitiatorService
from keycardai.mcp.client.auth.storage_facades import OAuthStorage
from keycardai.mcp.client.storage import InMemoryBackend, NamespacedStorage


@pytest.fixture
def oauth_storage():
    """Create OAuth storage for testing."""
    backend = InMemoryBackend()
    base_storage = NamespacedStorage(backend, "client:test:server:slack:connection:oauth")
    return OAuthStorage(base_storage)


@pytest.fixture
def auth_server_metadata():
    """Sample authorization server metadata."""
    return {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token",
        "registration_endpoint": "https://auth.example.com/register"
    }


@pytest.fixture
def client_info():
    """Sample client registration info."""
    return {
        "client_id": "test_client_123",
        "redirect_uris": ["http://localhost:8080/callback"],
        "grant_types": ["authorization_code", "refresh_token"]
    }


@pytest.fixture
def resource_url():
    """Sample resource URL."""
    return "https://api.example.com/resource"


# ===== Flow Initiation Tests =====


@pytest.mark.asyncio
async def test_initiate_flow_success(oauth_storage, auth_server_metadata, client_info, resource_url):
    """Test successful OAuth flow initiation."""
    service = OAuthFlowInitiatorService(storage=oauth_storage)

    # Initiate flow
    flow = await service.initiate_flow(
        auth_server_metadata=auth_server_metadata,
        client_info=client_info,
        resource_url=resource_url,
        server_name="test"
    )

    # Verify flow metadata
    assert isinstance(flow, FlowMetadata)
    assert flow.authorization_url.startswith("https://auth.example.com/authorize?")
    assert flow.state is not None
    assert len(flow.state) > 20  # Secure random state
    assert flow.resource_url == resource_url

    # Verify PKCE state was stored
    pkce_data = await oauth_storage.get_pkce_state(flow.state)
    assert pkce_data is not None
    assert "code_verifier" in pkce_data
    assert "code_challenge" in pkce_data
    assert pkce_data["resource_url"] == resource_url
    assert pkce_data["redirect_uri"] == client_info["redirect_uris"][0]


@pytest.mark.asyncio
async def test_authorization_url_parameters(oauth_storage, auth_server_metadata, client_info, resource_url):
    """Test that authorization URL contains all required parameters."""
    service = OAuthFlowInitiatorService(storage=oauth_storage)

    # Initiate flow
    flow = await service.initiate_flow(
        auth_server_metadata=auth_server_metadata,
        client_info=client_info,
        resource_url=resource_url,
        server_name="test"
    )

    # Parse authorization URL
    parsed = urlparse(flow.authorization_url)
    params = parse_qs(parsed.query)

    # Verify all required parameters
    assert params["response_type"][0] == "code"
    assert params["client_id"][0] == client_info["client_id"]
    assert params["redirect_uri"][0] == client_info["redirect_uris"][0]
    assert params["state"][0] == flow.state
    assert "code_challenge" in params
    assert params["code_challenge_method"][0] == "S256"
    assert params["resource"][0] == resource_url


@pytest.mark.asyncio
async def test_initiate_flow_with_scopes(oauth_storage, auth_server_metadata, client_info, resource_url):
    """Test flow initiation with custom scopes."""
    service = OAuthFlowInitiatorService(storage=oauth_storage)

    scopes = ["read", "write", "admin"]

    # Initiate flow with scopes
    flow = await service.initiate_flow(
        auth_server_metadata=auth_server_metadata,
        client_info=client_info,
        resource_url=resource_url,
        server_name="test",
        scopes=scopes
    )

    # Parse authorization URL
    parsed = urlparse(flow.authorization_url)
    params = parse_qs(parsed.query)

    # Verify scopes are included
    assert "scope" in params
    assert params["scope"][0] == "read write admin"


@pytest.mark.asyncio
async def test_initiate_flow_without_scopes(oauth_storage, auth_server_metadata, client_info, resource_url):
    """Test that scope parameter is optional."""
    service = OAuthFlowInitiatorService(storage=oauth_storage)

    # Initiate flow without scopes
    flow = await service.initiate_flow(
        auth_server_metadata=auth_server_metadata,
        client_info=client_info,
        resource_url=resource_url,
        server_name="test"
    )

    # Parse authorization URL
    parsed = urlparse(flow.authorization_url)
    params = parse_qs(parsed.query)

    # Scope should not be present
    assert "scope" not in params


@pytest.mark.asyncio
async def test_pkce_state_with_custom_ttl(oauth_storage, auth_server_metadata, client_info, resource_url):
    """Test PKCE state storage with custom TTL."""
    service = OAuthFlowInitiatorService(storage=oauth_storage)

    custom_ttl = timedelta(minutes=5)

    # Initiate flow with custom TTL
    flow = await service.initiate_flow(
        auth_server_metadata=auth_server_metadata,
        client_info=client_info,
        resource_url=resource_url,
        server_name="test",
        pkce_ttl=custom_ttl
    )

    # Verify PKCE state was stored (TTL is set in storage backend)
    pkce_data = await oauth_storage.get_pkce_state(flow.state)
    assert pkce_data is not None


@pytest.mark.asyncio
async def test_pkce_parameters_are_unique(oauth_storage, auth_server_metadata, client_info, resource_url):
    """Test that each flow generates unique PKCE parameters and state."""
    service = OAuthFlowInitiatorService(storage=oauth_storage)

    # Initiate two flows
    flow1 = await service.initiate_flow(
        auth_server_metadata=auth_server_metadata,
        client_info=client_info,
        resource_url=resource_url,
        server_name="test"
    )

    flow2 = await service.initiate_flow(
        auth_server_metadata=auth_server_metadata,
        client_info=client_info,
        resource_url=resource_url,
        server_name="test"
    )

    # States should be different
    assert flow1.state != flow2.state

    # PKCE parameters should be different
    pkce1 = await oauth_storage.get_pkce_state(flow1.state)
    pkce2 = await oauth_storage.get_pkce_state(flow2.state)

    assert pkce1["code_verifier"] != pkce2["code_verifier"]
    assert pkce1["code_challenge"] != pkce2["code_challenge"]


# ===== Error Handling Tests =====


@pytest.mark.asyncio
async def test_missing_authorization_endpoint(oauth_storage, client_info, resource_url):
    """Test error when authorization endpoint is missing."""
    service = OAuthFlowInitiatorService(storage=oauth_storage)

    bad_metadata = {
        "token_endpoint": "https://auth.example.com/token"
    }

    with pytest.raises(ValueError, match="Missing authorization_endpoint"):
        await service.initiate_flow(
            auth_server_metadata=bad_metadata,
            client_info=client_info,
            resource_url=resource_url,
            server_name="test"
        )


@pytest.mark.asyncio
async def test_missing_client_id(oauth_storage, auth_server_metadata, resource_url):
    """Test error when client_id is missing."""
    service = OAuthFlowInitiatorService(storage=oauth_storage)

    bad_client_info = {
        "redirect_uris": ["http://localhost:8080/callback"]
    }

    with pytest.raises(ValueError, match="Missing client_id"):
        await service.initiate_flow(
            auth_server_metadata=auth_server_metadata,
            client_info=bad_client_info,
            resource_url=resource_url,
            server_name="test"
        )


@pytest.mark.asyncio
async def test_missing_redirect_uris(oauth_storage, auth_server_metadata, resource_url):
    """Test error when redirect_uris are missing."""
    service = OAuthFlowInitiatorService(storage=oauth_storage)

    bad_client_info = {
        "client_id": "test_client_123"
    }

    with pytest.raises(ValueError, match="Missing redirect_uris"):
        await service.initiate_flow(
            auth_server_metadata=auth_server_metadata,
            client_info=bad_client_info,
            resource_url=resource_url,
            server_name="test"
        )


@pytest.mark.asyncio
async def test_empty_redirect_uris(oauth_storage, auth_server_metadata, resource_url):
    """Test error when redirect_uris list is empty."""
    service = OAuthFlowInitiatorService(storage=oauth_storage)

    bad_client_info = {
        "client_id": "test_client_123",
        "redirect_uris": []
    }

    with pytest.raises(ValueError, match="Missing redirect_uris"):
        await service.initiate_flow(
            auth_server_metadata=auth_server_metadata,
            client_info=bad_client_info,
            resource_url=resource_url,
            server_name="test"
        )


# ===== FlowMetadata Tests =====


def test_flow_metadata_initialization():
    """Test FlowMetadata initialization."""
    flow = FlowMetadata(
        authorization_url="https://auth.example.com/authorize?code=123",
        state="abc123",
        resource_url="https://api.example.com/resource"
    )

    assert flow.authorization_url == "https://auth.example.com/authorize?code=123"
    assert flow.state == "abc123"
    assert flow.resource_url == "https://api.example.com/resource"


# ===== URL Encoding Tests =====


@pytest.mark.asyncio
async def test_url_parameters_properly_encoded(oauth_storage, client_info):
    """Test that URL parameters with special characters are properly encoded."""
    service = OAuthFlowInitiatorService(storage=oauth_storage)

    # Use metadata and resource with special characters
    auth_server_metadata = {
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token"
    }

    resource_url = "https://api.example.com/resource?param=value&other=test"

    # Initiate flow
    flow = await service.initiate_flow(
        auth_server_metadata=auth_server_metadata,
        client_info=client_info,
        resource_url=resource_url,
        server_name="test"
    )

    # URL should be valid and parseable
    parsed = urlparse(flow.authorization_url)
    params = parse_qs(parsed.query)

    # Resource URL should be properly encoded
    assert params["resource"][0] == resource_url

