"""Tests for DelegationClient (server-to-server delegation over A2A JSONRPC)."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from keycardai.a2a import AgentServiceConfig, DelegationClient
from tests._helpers import NoopAgentExecutor


@pytest.fixture
def service_config():
    """Create test service configuration."""
    return AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone",
        agent_executor=NoopAgentExecutor(),
    )


@pytest.fixture
def a2a_client(service_config):
    """Create delegation client."""
    return DelegationClient(service_config)


@pytest.mark.asyncio
async def test_discover_service(a2a_client):
    """Test service discovery via agent card (a2a-sdk 1.x JSON shape)."""
    mock_response = Mock()
    mock_response.json.return_value = {
        "name": "Target Service",
        "description": "A test target service",
        "version": "1.0.0",
        "supportedInterfaces": [
            {
                "url": "https://target.example.com/a2a/jsonrpc",
                "protocolBinding": "jsonrpc",
                "protocolVersion": "1.0",
            }
        ],
        "capabilities": {"streaming": False},
        "skills": [{"id": "test_capability"}],
    }
    mock_response.raise_for_status = Mock()

    with patch.object(a2a_client.http_client, "get", return_value=mock_response):
        card = await a2a_client.discover_service("https://target.example.com")

    assert card["name"] == "Target Service"
    assert card["supportedInterfaces"][0]["url"].endswith("/a2a/jsonrpc")


@pytest.mark.asyncio
async def test_discover_service_invalid_card(a2a_client):
    """Discovery raises ValueError when the card has no name."""
    mock_response = Mock()
    mock_response.json.return_value = {
        # Missing 'name'
        "version": "1.0.0",
    }
    mock_response.raise_for_status = Mock()

    with patch.object(a2a_client.http_client, "get", return_value=mock_response):
        with pytest.raises(ValueError, match="missing required field 'name'"):
            await a2a_client.discover_service("https://target.example.com")


@pytest.mark.asyncio
async def test_get_delegation_token_with_subject(a2a_client):
    """Test token exchange with subject token."""
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

    call_args = mock_exchange.call_args[0][0]
    assert call_args.grant_type == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert call_args.subject_token == "user_token_456"
    assert call_args.resource == "https://target.example.com"


@pytest.mark.asyncio
async def test_get_delegation_token_client_credentials(a2a_client):
    """Test token exchange with client credentials."""
    mock_token_response = Mock()
    mock_token_response.access_token = "service_token_789"
    mock_token_response.expires_in = 3600

    with patch.object(
        a2a_client.oauth_client, "exchange_token", return_value=mock_token_response
    ) as mock_exchange:
        token = await a2a_client.get_delegation_token("https://target.example.com")

    assert token == "service_token_789"

    call_args = mock_exchange.call_args[0][0]
    assert call_args.grant_type == "client_credentials"
    assert call_args.resource == "https://target.example.com"


@pytest.mark.asyncio
async def test_invoke_service_posts_jsonrpc_envelope(a2a_client):
    """invoke_service sends a JSONRPC message/send to /a2a/jsonrpc."""
    mock_response = Mock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "role": "agent",
            "parts": [{"text": "Task completed successfully"}],
        },
    }
    mock_response.raise_for_status = Mock()

    with patch.object(
        a2a_client.http_client, "post", return_value=mock_response
    ) as mock_post:
        result = await a2a_client.invoke_service(
            "https://target.example.com",
            {"task": "Test task"},
            token="test_token_123",
        )

    # The wrapper unwraps the JSONRPC result back to the legacy shape.
    assert result["result"] == "Task completed successfully"
    assert result["delegation_chain"] == []

    # Confirm the request was a JSONRPC envelope to /a2a/jsonrpc.
    posted_url = mock_post.call_args[0][0]
    posted_body = mock_post.call_args[1]["json"]
    assert posted_url == "https://target.example.com/a2a/jsonrpc"
    assert posted_body["jsonrpc"] == "2.0"
    assert posted_body["method"] == "message/send"
    assert posted_body["params"]["message"]["parts"][0]["text"] == "Test task"


@pytest.mark.asyncio
async def test_invoke_service_auto_token_exchange(a2a_client):
    """invoke_service triggers token exchange when no token is supplied."""
    mock_token_response = Mock()
    mock_token_response.access_token = "auto_token_123"
    mock_token_response.expires_in = 3600

    mock_http_response = Mock()
    mock_http_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {"role": "agent", "parts": [{"text": "Success"}]},
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
            )

    assert result["result"] == "Success"
    call_kwargs = mock_post.call_args[1]
    assert call_kwargs["headers"]["Authorization"] == "Bearer auto_token_123"


@pytest.mark.asyncio
async def test_invoke_service_string_task(a2a_client):
    """A string task lands as the message text in the JSONRPC envelope."""
    mock_response = Mock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {"role": "agent", "parts": [{"text": "Done"}]},
    }
    mock_response.raise_for_status = Mock()

    with patch.object(
        a2a_client.http_client, "post", return_value=mock_response
    ) as mock_post:
        await a2a_client.invoke_service(
            "https://target.example.com",
            "Do something",
            token="test_token",
        )

    posted_body = mock_post.call_args[1]["json"]
    assert posted_body["params"]["message"]["parts"][0]["text"] == "Do something"


@pytest.mark.asyncio
async def test_invoke_service_jsonrpc_error_raises(a2a_client):
    """A JSONRPC error response surfaces as a ValueError."""
    mock_response = Mock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "error": {"code": -32600, "message": "Invalid Request"},
    }
    mock_response.raise_for_status = Mock()

    with patch.object(a2a_client.http_client, "post", return_value=mock_response):
        with pytest.raises(ValueError, match="JSONRPC error"):
            await a2a_client.invoke_service(
                "https://target.example.com",
                "Anything",
                token="test_token",
            )


@pytest.mark.asyncio
async def test_context_manager(service_config):
    """Test A2A client as context manager."""
    async with DelegationClient(service_config) as client:
        assert client is not None


@pytest.mark.asyncio
async def test_close(a2a_client):
    """Test client cleanup."""
    a2a_client.http_client.aclose = AsyncMock()
    await a2a_client.close()
    a2a_client.http_client.aclose.assert_called_once()
