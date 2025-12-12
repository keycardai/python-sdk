"""Tests for agent card server endpoints and token validation."""

from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from keycardai.agents import AgentServiceConfig, create_agent_card_server


@pytest.fixture
def service_config():
    """Create test service configuration."""
    return AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone_123",
        description="Test service for unit tests",
        capabilities=["test_capability", "another_capability"],
    )


@pytest.fixture
def mock_crew_factory():
    """Mock crew factory that returns a simple crew."""

    def factory():
        crew = Mock()
        crew.kickoff.return_value = "Test crew execution result"
        return crew

    return factory


@pytest.fixture
def app(service_config):
    """Create test FastAPI app without crew factory."""
    return create_agent_card_server(service_config)


@pytest.fixture
def app_with_crew(service_config, mock_crew_factory):
    """Create test FastAPI app with crew factory."""
    config = service_config
    config.crew_factory = mock_crew_factory
    return create_agent_card_server(config)


@pytest.fixture
def client(app):
    """Create test client without crew."""
    return TestClient(app)


@pytest.fixture
def client_with_crew(app_with_crew):
    """Create test client with crew."""
    return TestClient(app_with_crew)


@pytest.fixture
def mock_valid_token_data():
    """Mock valid token data from JWT verification."""
    return {
        "sub": "user_123",
        "client_id": "calling_service",
        "aud": ["https://test.example.com"],
        "iss": "https://test_zone_123.keycard.cloud",
        "exp": 9999999999,  # Far future
        "iat": 1700000000,
        "delegation_chain": ["service1"],
    }


@pytest.fixture
def mock_expired_token_data():
    """Mock expired token data."""
    return {
        "sub": "user_123",
        "aud": ["https://test.example.com"],
        "iss": "https://test_zone_123.keycard.cloud",
        "exp": 1000000000,  # Past
        "iat": 900000000,
    }


@pytest.fixture
def mock_wrong_audience_token_data():
    """Mock token with wrong audience."""
    return {
        "sub": "user_123",
        "aud": ["https://wrong.example.com"],
        "iss": "https://test_zone_123.keycard.cloud",
        "exp": 9999999999,
        "iat": 1700000000,
    }


@pytest.fixture
def mock_wrong_issuer_token_data():
    """Mock token with wrong issuer."""
    return {
        "sub": "user_123",
        "aud": ["https://test.example.com"],
        "iss": "https://wrong_zone.keycard.cloud",
        "exp": 9999999999,
        "iat": 1700000000,
    }


class TestAgentCardEndpoint:
    """Test /.well-known/agent-card.json endpoint."""

    def test_get_agent_card_returns_200(self, client):
        """Test agent card endpoint returns 200 OK."""
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200

    def test_agent_card_has_required_fields(self, client):
        """Test agent card contains all required fields."""
        response = client.get("/.well-known/agent-card.json")
        data = response.json()

        # Check required fields
        assert "name" in data
        assert "description" in data
        assert "type" in data
        assert "identity" in data
        assert "capabilities" in data
        assert "endpoints" in data
        assert "auth" in data

        # Check endpoint structure
        assert "invoke" in data["endpoints"]
        assert "status" in data["endpoints"]

        # Check auth structure
        assert "type" in data["auth"]
        assert "token_url" in data["auth"]
        assert "resource" in data["auth"]

    def test_agent_card_matches_config(self, client, service_config):
        """Test agent card content matches service config."""
        response = client.get("/.well-known/agent-card.json")
        data = response.json()

        assert data["name"] == service_config.service_name
        assert data["description"] == service_config.description
        assert data["identity"] == service_config.identity_url
        assert data["capabilities"] == service_config.capabilities
        assert data["type"] == "crew_service"

    def test_agent_card_is_publicly_accessible(self, client):
        """Test agent card endpoint doesn't require authentication."""
        # No Authorization header
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200


class TestStatusEndpoint:
    """Test /status endpoint."""

    def test_status_returns_200(self, client):
        """Test status endpoint returns 200 OK."""
        response = client.get("/status")
        assert response.status_code == 200

    def test_status_returns_healthy(self, client):
        """Test status endpoint returns healthy status."""
        response = client.get("/status")
        data = response.json()

        assert data["status"] == "healthy"
        assert "service" in data
        assert "identity" in data
        assert "version" in data

    def test_status_is_publicly_accessible(self, client):
        """Test status endpoint doesn't require authentication."""
        # No Authorization header
        response = client.get("/status")
        assert response.status_code == 200


class TestInvokeEndpoint:
    """Test /invoke endpoint with authentication."""

    def test_invoke_requires_authorization_header(self, client):
        """Test invoke endpoint requires Authorization header."""
        response = client.post("/invoke", json={"task": "test task"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing or invalid Authorization header"

    def test_invoke_rejects_missing_bearer_prefix(self, client):
        """Test invoke rejects authorization without Bearer prefix."""
        response = client.post(
            "/invoke",
            json={"task": "test task"},
            headers={"Authorization": "invalid_token"},
        )
        assert response.status_code == 401

    @patch("keycardai.agents.agent_card_server.get_verification_key")
    @patch("keycardai.agents.agent_card_server.decode_and_verify_jwt")
    def test_invoke_with_valid_token_but_no_crew_factory(
        self,
        mock_decode_jwt,
        mock_get_key,
        client,
        mock_valid_token_data,
    ):
        """Test invoke with valid token but no crew factory returns 501."""
        mock_get_key.return_value = "mock_public_key"
        mock_decode_jwt.return_value = mock_valid_token_data

        response = client.post(
            "/invoke",
            json={"task": "test task"},
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 501
        assert "No crew factory" in response.json()["detail"]

    @patch("keycardai.agents.agent_card_server.get_verification_key")
    @patch("keycardai.agents.agent_card_server.decode_and_verify_jwt")
    @patch("time.time")
    def test_invoke_with_valid_token_executes_crew(
        self,
        mock_time,
        mock_decode_jwt,
        mock_get_key,
        client_with_crew,
        mock_valid_token_data,
        mock_crew_factory,
    ):
        """Test invoke with valid token successfully executes crew."""
        mock_time.return_value = 1700000000  # Before expiration
        mock_get_key.return_value = "mock_public_key"
        mock_decode_jwt.return_value = mock_valid_token_data

        response = client_with_crew.post(
            "/invoke",
            json={"task": "analyze this PR"},
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "result" in data
        assert "delegation_chain" in data
        assert data["result"] == "Test crew execution result"

    @patch("keycardai.agents.agent_card_server.get_verification_key")
    @patch("keycardai.agents.agent_card_server.decode_and_verify_jwt")
    @patch("time.time")
    def test_invoke_updates_delegation_chain(
        self,
        mock_time,
        mock_decode_jwt,
        mock_get_key,
        client_with_crew,
        mock_valid_token_data,
    ):
        """Test invoke adds service to delegation chain."""
        mock_time.return_value = 1700000000
        mock_get_key.return_value = "mock_public_key"
        mock_decode_jwt.return_value = mock_valid_token_data

        response = client_with_crew.post(
            "/invoke",
            json={"task": "test"},
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200
        data = response.json()
        # Should append test_client to existing chain
        assert "test_client" in data["delegation_chain"]
        assert data["delegation_chain"][-1] == "test_client"

    @patch("keycardai.agents.agent_card_server.get_verification_key")
    @patch("keycardai.agents.agent_card_server.decode_and_verify_jwt")
    @patch("time.time")
    def test_invoke_with_dict_task(
        self,
        mock_time,
        mock_decode_jwt,
        mock_get_key,
        client_with_crew,
        mock_valid_token_data,
    ):
        """Test invoke with task as dictionary."""
        mock_time.return_value = 1700000000
        mock_get_key.return_value = "mock_public_key"
        mock_decode_jwt.return_value = mock_valid_token_data

        response = client_with_crew.post(
            "/invoke",
            json={"task": {"repo": "test/repo", "pr_number": 123}},
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 200

    @patch("keycardai.agents.agent_card_server.get_verification_key")
    @patch("keycardai.agents.agent_card_server.decode_and_verify_jwt")
    @patch("time.time")
    def test_invoke_crew_exception_returns_500(
        self,
        mock_time,
        mock_decode_jwt,
        mock_get_key,
        service_config,
        mock_valid_token_data,
    ):
        """Test invoke returns 500 when crew execution fails."""
        mock_time.return_value = 1700000000
        mock_get_key.return_value = "mock_public_key"
        mock_decode_jwt.return_value = mock_valid_token_data

        # Create a crew factory that returns a crew that raises an exception
        def failing_crew_factory():
            crew = Mock()
            crew.kickoff.side_effect = RuntimeError("Crew execution failed")
            return crew

        config = service_config
        config.crew_factory = failing_crew_factory
        app = create_agent_card_server(config)
        client = TestClient(app)

        response = client.post(
            "/invoke",
            json={"task": "test"},
            headers={"Authorization": "Bearer valid_token"},
        )

        assert response.status_code == 500
        assert "Crew execution failed" in response.json()["detail"]


class TestTokenValidation:
    """Test token validation logic."""

    @patch("keycardai.agents.agent_card_server.get_verification_key")
    @patch("keycardai.agents.agent_card_server.decode_and_verify_jwt")
    @patch("time.time")
    def test_validate_token_with_expired_token(
        self,
        mock_time,
        mock_decode_jwt,
        mock_get_key,
        client,
        mock_expired_token_data,
    ):
        """Test token validation rejects expired token."""
        mock_time.return_value = 2000000000  # After expiration
        mock_get_key.return_value = "mock_public_key"
        mock_decode_jwt.return_value = mock_expired_token_data

        response = client.post(
            "/invoke",
            json={"task": "test"},
            headers={"Authorization": "Bearer expired_token"},
        )

        assert response.status_code == 401
        assert "expired" in response.json()["detail"].lower()

    @patch("keycardai.agents.agent_card_server.get_verification_key")
    @patch("keycardai.agents.agent_card_server.decode_and_verify_jwt")
    @patch("time.time")
    def test_validate_token_audience_mismatch(
        self,
        mock_time,
        mock_decode_jwt,
        mock_get_key,
        client,
        mock_wrong_audience_token_data,
    ):
        """Test token validation rejects wrong audience."""
        mock_time.return_value = 1700000000
        mock_get_key.return_value = "mock_public_key"
        mock_decode_jwt.return_value = mock_wrong_audience_token_data

        response = client.post(
            "/invoke",
            json={"task": "test"},
            headers={"Authorization": "Bearer wrong_aud_token"},
        )

        assert response.status_code == 403
        assert "audience mismatch" in response.json()["detail"].lower()

    @patch("keycardai.agents.agent_card_server.get_verification_key")
    @patch("keycardai.agents.agent_card_server.decode_and_verify_jwt")
    @patch("time.time")
    def test_validate_token_issuer_mismatch(
        self,
        mock_time,
        mock_decode_jwt,
        mock_get_key,
        client,
        mock_wrong_issuer_token_data,
    ):
        """Test token validation rejects wrong issuer."""
        mock_time.return_value = 1700000000
        mock_get_key.return_value = "mock_public_key"
        mock_decode_jwt.return_value = mock_wrong_issuer_token_data

        response = client.post(
            "/invoke",
            json={"task": "test"},
            headers={"Authorization": "Bearer wrong_iss_token"},
        )

        assert response.status_code == 401
        assert "issuer mismatch" in response.json()["detail"].lower()

    @patch("keycardai.agents.agent_card_server.get_verification_key")
    @patch("keycardai.agents.agent_card_server.decode_and_verify_jwt")
    @patch("time.time")
    def test_validate_token_missing_audience(
        self,
        mock_time,
        mock_decode_jwt,
        mock_get_key,
        client,
    ):
        """Test token validation rejects token without audience."""
        mock_time.return_value = 1700000000
        mock_get_key.return_value = "mock_public_key"
        mock_decode_jwt.return_value = {
            "sub": "user_123",
            "iss": "https://test_zone_123.keycard.cloud",
            "exp": 9999999999,
            # Missing "aud" field
        }

        response = client.post(
            "/invoke",
            json={"task": "test"},
            headers={"Authorization": "Bearer no_aud_token"},
        )

        assert response.status_code == 401
        assert "missing audience" in response.json()["detail"].lower()

    @patch("keycardai.agents.agent_card_server.get_verification_key")
    def test_validate_token_invalid_jwt_signature(
        self,
        mock_get_key,
        client,
    ):
        """Test token validation rejects invalid JWT signature."""
        mock_get_key.return_value = "mock_public_key"

        # decode_and_verify_jwt will raise ValueError for invalid signature
        with patch("keycardai.agents.agent_card_server.decode_and_verify_jwt") as mock_decode:
            mock_decode.side_effect = ValueError("JWT verification failed")

            response = client.post(
                "/invoke",
                json={"task": "test"},
                headers={"Authorization": "Bearer invalid_signature_token"},
            )

            assert response.status_code == 401
            assert "Invalid token" in response.json()["detail"]

    @patch("keycardai.agents.agent_card_server.get_verification_key")
    @patch("keycardai.agents.agent_card_server.decode_and_verify_jwt")
    @patch("time.time")
    def test_validate_token_handles_string_audience(
        self,
        mock_time,
        mock_decode_jwt,
        mock_get_key,
        client_with_crew,
    ):
        """Test token validation handles audience as string (not list)."""
        mock_time.return_value = 1700000000
        mock_get_key.return_value = "mock_public_key"
        mock_decode_jwt.return_value = {
            "sub": "user_123",
            "aud": "https://test.example.com",  # String, not list
            "iss": "https://test_zone_123.keycard.cloud",
            "exp": 9999999999,
            "delegation_chain": [],
        }

        response = client_with_crew.post(
            "/invoke",
            json={"task": "test"},
            headers={"Authorization": "Bearer string_aud_token"},
        )

        assert response.status_code == 200
