"""Tests for A2AServiceClient."""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from keycardai.agents import AgentServiceConfig, A2AServiceClient


@pytest.fixture
def service_config():
    """Create test service configuration."""
    return AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone",
    )


@pytest.fixture
def a2a_client(service_config):
    """Create A2A client."""
    return A2AServiceClient(service_config)


@pytest.mark.asyncio
async def test_discover_service(a2a_client):
    """Test service discovery via agent card."""
    # Mock HTTP response
    mock_response = Mock()
    mock_response.json.return_value = {
        "name": "Target Service",
        "description": "A test target service",
        "endpoints": {"invoke": "https://target.example.com/invoke"},
        "auth": {"type": "oauth2"},
        "capabilities": ["test_capability"],
    }
    mock_response.raise_for_status = Mock()

    with patch.object(a2a_client.http_client, "get", return_value=mock_response):
        card = await a2a_client.discover_service("https://target.example.com")

    assert card["name"] == "Target Service"
    assert card["capabilities"] == ["test_capability"]
    assert "invoke" in card["endpoints"]


@pytest.mark.asyncio
async def test_discover_service_invalid_card(a2a_client):
    """Test error handling for invalid agent card."""
    # Mock HTTP response with missing required fields
    mock_response = Mock()
    mock_response.json.return_value = {
        "name": "Target Service",
        # Missing 'endpoints' and 'auth'
    }
    mock_response.raise_for_status = Mock()

    with patch.object(a2a_client.http_client, "get", return_value=mock_response):
        with pytest.raises(ValueError, match="missing required field"):
            await a2a_client.discover_service("https://target.example.com")


@pytest.mark.asyncio
async def test_get_delegation_token_with_subject(a2a_client):
    """Test token exchange with subject token."""
    # Mock OAuth response
    mock_token_response = Mock()
    mock_token_response.access_token = "delegated_token_123"
    mock_token_response.expires_in = 3600

    with patch.object(
        a2a_client.oauth_client, "exchange_token", return_value=mock_token_response
    ) as mock_exchange:
        token = await a2a_client.get_delegation_token(
            "https://target.example.com",
            subject_token="user_token_456",
        )

    assert token == "delegated_token_123"

    # Verify exchange_token was called with correct parameters
    call_args = mock_exchange.call_args[0][0]
    assert call_args.grant_type == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert call_args.subject_token == "user_token_456"
    assert call_args.resource == "https://target.example.com"


@pytest.mark.asyncio
async def test_get_delegation_token_client_credentials(a2a_client):
    """Test token exchange with client credentials."""
    # Mock OAuth response
    mock_token_response = Mock()
    mock_token_response.access_token = "service_token_789"
    mock_token_response.expires_in = 3600

    with patch.object(
        a2a_client.oauth_client, "exchange_token", return_value=mock_token_response
    ) as mock_exchange:
        token = await a2a_client.get_delegation_token("https://target.example.com")

    assert token == "service_token_789"

    # Verify exchange_token was called with client_credentials grant
    call_args = mock_exchange.call_args[0][0]
    assert call_args.grant_type == "client_credentials"
    assert call_args.resource == "https://target.example.com"


@pytest.mark.asyncio
async def test_invoke_service(a2a_client):
    """Test service invocation."""
    # Mock HTTP response
    mock_response = Mock()
    mock_response.json.return_value = {
        "result": "Task completed successfully",
        "delegation_chain": ["test_client", "target_service"],
    }
    mock_response.raise_for_status = Mock()

    with patch.object(a2a_client.http_client, "post", return_value=mock_response):
        result = await a2a_client.invoke_service(
            "https://target.example.com",
            {"task": "Test task"},
            token="test_token_123",
        )

    assert result["result"] == "Task completed successfully"
    assert result["delegation_chain"] == ["test_client", "target_service"]


@pytest.mark.asyncio
async def test_invoke_service_auto_token_exchange(a2a_client):
    """Test service invocation with automatic token exchange."""
    # Mock token exchange
    mock_token_response = Mock()
    mock_token_response.access_token = "auto_token_123"
    mock_token_response.expires_in = 3600

    # Mock HTTP response
    mock_http_response = Mock()
    mock_http_response.json.return_value = {
        "result": "Success",
        "delegation_chain": ["test_client"],
    }
    mock_http_response.raise_for_status = Mock()

    with patch.object(
        a2a_client.oauth_client, "exchange_token", return_value=mock_token_response
    ):
        with patch.object(
            a2a_client.http_client, "post", return_value=mock_http_response
        ) as mock_post:
            result = await a2a_client.invoke_service(
                "https://target.example.com",
                "Simple task string",
                # No token provided - should trigger automatic exchange
            )

    assert result["result"] == "Success"

    # Verify POST was called with auto-obtained token
    call_kwargs = mock_post.call_args[1]
    assert call_kwargs["headers"]["Authorization"] == "Bearer auto_token_123"


@pytest.mark.asyncio
async def test_invoke_service_string_task(a2a_client):
    """Test service invocation with string task."""
    mock_response = Mock()
    mock_response.json.return_value = {"result": "Done", "delegation_chain": []}
    mock_response.raise_for_status = Mock()

    with patch.object(
        a2a_client.http_client, "post", return_value=mock_response
    ) as mock_post:
        await a2a_client.invoke_service(
            "https://target.example.com",
            "Do something",  # String task
            token="test_token",
        )

    # Verify task was converted to dict format
    call_kwargs = mock_post.call_args[1]
    assert call_kwargs["json"]["task"] == "Do something"


@pytest.mark.asyncio
async def test_context_manager(service_config):
    """Test A2A client as context manager."""
    async with A2AServiceClient(service_config) as client:
        assert client is not None

    # HTTP client should be closed after context exit
    # (In practice, this would be verified by checking connection state)


@pytest.mark.asyncio
async def test_close(a2a_client):
    """Test client cleanup."""
    # Mock the http_client.aclose method
    a2a_client.http_client.aclose = AsyncMock()

    await a2a_client.close()

    a2a_client.http_client.aclose.assert_called_once()
