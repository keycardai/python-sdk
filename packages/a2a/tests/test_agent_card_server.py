"""Tests for the Keycard-protected agent server: public routes and auth gate."""

import pytest
from keycardai.starlette import KeycardUser
from starlette.requests import Request
from starlette.testclient import TestClient

from keycardai.a2a import AgentServiceConfig, create_agent_card_server
from keycardai.a2a.server.app import _KeycardServerCallContextBuilder


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
    """The `/a2a/jsonrpc` endpoint must reject anonymous requests with 401.

    A 401 means `_EagerKeycardAuthBackend` caught the missing/malformed
    auth before the JSONRPC dispatcher saw the body. Any other status
    (400, 200, etc.) means the gate let the request through and the
    dispatcher handled it; that is a regression we want to fail loudly on.
    """

    def test_jsonrpc_requires_authorization(self, client):
        response = client.post(
            "/a2a/jsonrpc",
            json={"jsonrpc": "2.0", "id": "1", "method": "message/send", "params": {}},
        )
        assert response.status_code == 401
        # RFC 6750 challenge: WWW-Authenticate header with Bearer scheme.
        assert response.headers["www-authenticate"].startswith("Bearer")

    def test_jsonrpc_rejects_malformed_authorization(self, client):
        response = client.post(
            "/a2a/jsonrpc",
            json={"jsonrpc": "2.0", "id": "1", "method": "message/send", "params": {}},
            headers={"Authorization": "not-a-bearer-token"},
        )
        # Malformed scheme returns 400 per the underlying backend's contract;
        # the body is still rejected before reaching the dispatcher, which is
        # the only thing we care about here.
        assert response.status_code in (400, 401)


class TestKeycardServerCallContextBuilder:
    """The custom context builder is the bridge between Keycard auth and a2a-sdk.

    It must propagate the verified KeycardUser plus the bare access_token
    from the Starlette request into ServerCallContext.state where the
    agent executor reads them for downstream delegated token exchange.
    """

    def _make_request(self, *, user) -> Request:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/a2a/jsonrpc",
            "headers": [],
            "query_string": b"",
            "scheme": "https",
            "server": ("test.example.com", 443),
            "user": user,
        }
        return Request(scope)

    def test_builder_propagates_keycard_user_and_access_token(self):
        user = KeycardUser(
            access_token="t-deadbeef",
            client_id="caller-svc",
            zone_id="abc123",
            resource_server_url="https://test.example.com",
            scopes=["mcp:tools"],
        )
        request = self._make_request(user=user)

        ctx = _KeycardServerCallContextBuilder().build(request)

        assert ctx.state.get("access_token") == "t-deadbeef"
        assert ctx.state.get("keycard_user") is user

    def test_builder_omits_access_token_for_non_keycard_user(self):
        # When the upstream middleware did not produce a KeycardUser
        # (e.g. an older test setup), the builder must not fabricate state
        # entries: an executor reading ``state["access_token"]`` should
        # then see ``None`` rather than a token from a different request.
        from starlette.authentication import UnauthenticatedUser

        request = self._make_request(user=UnauthenticatedUser())

        ctx = _KeycardServerCallContextBuilder().build(request)

        assert "access_token" not in ctx.state
        assert "keycard_user" not in ctx.state


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
