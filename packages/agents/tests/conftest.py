"""Pytest configuration for agents tests."""

import pytest
from unittest.mock import Mock, AsyncMock

from keycardai.agents import AgentServiceConfig


# ============================================
# Basic Configuration Fixtures
# ============================================

@pytest.fixture
def mock_zone_id():
    """Mock Keycard zone ID."""
    return "test_zone_123"


@pytest.fixture
def mock_service_url():
    """Mock service URL."""
    return "https://test.example.com"


@pytest.fixture
def mock_identity_url():
    """Mock identity URL."""
    return "https://identity.example.com"


# ============================================
# Service Configuration Fixtures
# ============================================

@pytest.fixture
def service_config(mock_zone_id, mock_identity_url):
    """Create test service configuration with minimal settings."""
    return AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url=mock_identity_url,
        zone_id=mock_zone_id,
    )


@pytest.fixture
def service_config_with_capabilities(mock_zone_id, mock_identity_url):
    """Create test service configuration with capabilities."""
    return AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url=mock_identity_url,
        zone_id=mock_zone_id,
        description="Test service for unit tests",
        capabilities=["test_capability", "another_capability"],
    )


# ============================================
# OAuth Mocking Fixtures
# ============================================

@pytest.fixture
def mock_oauth_client():
    """Mock OAuth client for token operations."""
    from keycardai.oauth.types.models import TokenResponse

    client = Mock()

    def mock_exchange_token(**kwargs):
        return TokenResponse(
            access_token="test_token_123",
            token_type="Bearer",
            expires_in=3600,
        )

    client.exchange_token.return_value = mock_exchange_token()
    return client


@pytest.fixture
def mock_async_oauth_client():
    """Mock async OAuth client for token operations."""
    from keycardai.oauth.types.models import TokenResponse

    client = AsyncMock()

    async def mock_exchange_token(**kwargs):
        return TokenResponse(
            access_token="test_token_123",
            token_type="Bearer",
            expires_in=3600,
        )

    client.exchange_token.side_effect = mock_exchange_token
    return client


# ============================================
# HTTP Client Mocking Fixtures
# ============================================

@pytest.fixture
def mock_http_client():
    """Mock HTTP client for service calls."""
    client = Mock()

    mock_response = Mock()
    mock_response.json.return_value = {
        "name": "Test Service",
        "description": "Test description",
        "endpoints": {"invoke": "https://test.example.com/invoke"},
        "auth": {"type": "oauth2"},
        "capabilities": ["test_capability"],
    }
    mock_response.raise_for_status = Mock()
    mock_response.status_code = 200

    client.get.return_value = mock_response
    client.post.return_value = mock_response

    return client


@pytest.fixture
def mock_async_http_client():
    """Mock async HTTP client for service calls."""
    client = AsyncMock()

    mock_response = AsyncMock()
    mock_response.json.return_value = {
        "name": "Test Service",
        "description": "Test description",
        "endpoints": {"invoke": "https://test.example.com/invoke"},
        "auth": {"type": "oauth2"},
        "capabilities": ["test_capability"],
    }
    mock_response.raise_for_status = AsyncMock()
    mock_response.status_code = 200

    client.get.return_value = mock_response
    client.post.return_value = mock_response

    return client


# ============================================
# Agent Card Fixtures
# ============================================

@pytest.fixture
def mock_agent_card():
    """Mock agent card response."""
    return {
        "name": "Target Service",
        "description": "A test target service",
        "type": "crew_service",
        "identity": "https://target.example.com",
        "endpoints": {
            "invoke": "https://target.example.com/invoke",
            "status": "https://target.example.com/status",
        },
        "auth": {
            "type": "oauth2",
            "token_url": "https://test_zone.keycard.cloud/oauth/token",
            "resource": "https://target.example.com",
        },
        "capabilities": ["test_capability"],
    }


@pytest.fixture
def mock_agent_card_minimal():
    """Mock minimal agent card with only required fields."""
    return {
        "name": "Minimal Service",
        "endpoints": {
            "invoke": "https://minimal.example.com/invoke",
        },
        "auth": {
            "type": "oauth2",
        },
    }


# ============================================
# Service Discovery Fixtures
# ============================================

@pytest.fixture
def mock_delegatable_services():
    """Mock list of delegatable services."""
    return [
        {
            "name": "Service One",
            "url": "https://service1.example.com",
            "description": "First test service",
            "capabilities": ["capability1", "capability2"],
        },
        {
            "name": "Service Two",
            "url": "https://service2.example.com",
            "description": "Second test service",
            "capabilities": ["capability3"],
        },
    ]


# ============================================
# JWT Token Fixtures
# ============================================

@pytest.fixture
def mock_jwt_token_data():
    """Mock JWT token data with standard claims."""
    return {
        "sub": "user_123",
        "client_id": "calling_service",
        "aud": ["https://test.example.com"],
        "iss": "https://test_zone_123.keycard.cloud",
        "delegation_chain": ["service1", "service2"],
        "exp": 9999999999,  # Far future
        "iat": 1700000000,
    }


@pytest.fixture
def mock_jwt_token_invalid_audience():
    """Mock JWT token with wrong audience."""
    return {
        "sub": "user_123",
        "aud": ["https://wrong.example.com"],
        "iss": "https://test_zone_123.keycard.cloud",
        "exp": 9999999999,
    }


@pytest.fixture
def mock_jwt_token_expired():
    """Mock expired JWT token."""
    return {
        "sub": "user_123",
        "aud": ["https://test.example.com"],
        "iss": "https://test_zone_123.keycard.cloud",
        "exp": 1000000000,  # Past expiration
        "iat": 900000000,
    }


# ============================================
# Crew Factory Fixtures
# ============================================

@pytest.fixture
def mock_crew():
    """Mock CrewAI crew instance."""
    crew = Mock()
    crew.kickoff.return_value = "Test crew execution result"
    return crew


@pytest.fixture
def mock_crew_factory(mock_crew):
    """Mock CrewAI crew factory function."""

    def factory():
        return mock_crew

    return factory


@pytest.fixture
def mock_crew_that_raises():
    """Mock crew that raises exception on kickoff."""
    crew = Mock()
    crew.kickoff.side_effect = RuntimeError("Crew execution failed")
    return crew
