"""Tests for the Keycard-protected agent server: public routes and auth gate."""

import pytest
from starlette.testclient import TestClient

from keycardai.a2a import AgentServiceConfig, create_agent_card_server


@pytest.fixture
def service_config():
    """Create test service configuration."""
    from tests._helpers import NoopAgentExecutor

    return AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone_123",
        description="Test service for unit tests",
        capabilities=["test_capability", "another_capability"],
        agent_executor=NoopAgentExecutor(),
    )


@pytest.fixture
def app(service_config):
    """Create test Starlette app."""
    return create_agent_card_server(service_config)


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


class TestAgentCardEndpoint:
    """Tests for `/.well-known/agent-card.json`, served by a2a-sdk's route factory."""

    def test_get_agent_card_returns_200(self, client):
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200

    def test_agent_card_publicly_accessible(self, client):
        # No Authorization header on this public endpoint.
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200

    def test_agent_card_carries_core_fields(self, client, service_config):
        response = client.get("/.well-known/agent-card.json")
        data = response.json()
        assert data["name"] == service_config.service_name
        assert data["version"]
        assert "capabilities" in data
        assert "skills" in data
        # One skill per declared capability.
        assert len(data["skills"]) == len(service_config.capabilities)

    def test_agent_card_supported_interfaces_includes_jsonrpc(self, client, service_config):
        response = client.get("/.well-known/agent-card.json")
        data = response.json()
        # a2a-sdk's MessageToDict emits the protobuf field as camelCase.
        interfaces = data.get("supportedInterfaces") or data.get("supported_interfaces") or []
        assert any(
            iface.get("url", "").endswith("/a2a/jsonrpc")
            for iface in interfaces
        )


class TestStatusEndpoint:
    """Tests for `/status` health check (public)."""

    def test_status_returns_200(self, client):
        response = client.get("/status")
        assert response.status_code == 200

    def test_status_returns_healthy_payload(self, client):
        response = client.get("/status")
        data = response.json()
        assert data["status"] == "healthy"
        assert "service" in data
        assert "identity" in data
        assert "version" in data

    def test_status_publicly_accessible(self, client):
        response = client.get("/status")
        assert response.status_code == 200


class TestJsonRpcAuthGate:
    """The `/a2a/jsonrpc` endpoint must reject anonymous requests."""

    def test_jsonrpc_requires_authorization(self, client):
        response = client.post(
            "/a2a/jsonrpc",
            json={"jsonrpc": "2.0", "id": "1", "method": "message/send", "params": {}},
        )
        assert response.status_code in (400, 401)

    def test_jsonrpc_rejects_malformed_authorization(self, client):
        response = client.post(
            "/a2a/jsonrpc",
            json={"jsonrpc": "2.0", "id": "1", "method": "message/send", "params": {}},
            headers={"Authorization": "not-a-bearer-token"},
        )
        assert response.status_code in (400, 401)


class TestOAuthMetadataEndpoints:
    """Tests for the `/.well-known/oauth-*` discovery endpoints."""

    def test_protected_resource_metadata(self, client):
        response = client.get("/.well-known/oauth-protected-resource")
        assert response.status_code == 200
        data = response.json()
        assert "authorization_servers" in data

    def test_authorization_server_metadata(self, client):
        response = client.get("/.well-known/oauth-authorization-server")
        # The endpoint proxies to the auth server which is unreachable in tests.
        # Accept any non-error response shape.
        assert response.status_code in (200, 302, 307, 503)
