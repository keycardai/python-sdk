"""Tests for the keycardai-a2a server primitives.

The package no longer ships a one-call server factory; tests here build a
Starlette app inline from the primitives, mirroring the composition a
customer would write in their own setup. See
``packages/a2a/examples/keycard_protected_server/main.py`` for the
canonical reference composition.
"""

import pytest
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from keycardai.oauth.server.credentials import ClientSecret
from keycardai.starlette import AuthProvider, KeycardUser, keycard_on_error
from keycardai.starlette.routers.metadata import (
    well_known_authorization_server_route,
    well_known_protected_resource_route,
)
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.routing import Mount
from starlette.testclient import TestClient

from keycardai.a2a import (
    AgentServiceConfig,
    EagerKeycardAuthBackend,
    KeycardServerCallContextBuilder,
    build_agent_card_from_config,
)


@pytest.fixture
def service_config():
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
def app(service_config):
    """Compose a Starlette app from the primitives.

    Mirrors the example at ``examples/keycard_protected_server/main.py``;
    keep the two in sync.
    """
    from tests._helpers import NoopAgentExecutor

    auth_provider = AuthProvider(
        zone_url=service_config.auth_server_url,
        server_name=service_config.service_name,
        server_url=service_config.identity_url,
        application_credential=ClientSecret(
            (service_config.client_id, service_config.client_secret)
        ),
    )
    verifier = auth_provider.get_token_verifier()

    agent_card = build_agent_card_from_config(service_config)
    request_handler = DefaultRequestHandler(
        agent_executor=NoopAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    return Starlette(
        routes=[
            *create_agent_card_routes(agent_card=agent_card),
            well_known_protected_resource_route(
                issuer=service_config.auth_server_url,
                resource="/.well-known/oauth-protected-resource{resource_path:path}",
            ),
            well_known_authorization_server_route(
                issuer=service_config.auth_server_url,
                resource="/.well-known/oauth-authorization-server{resource_path:path}",
            ),
            Mount(
                "/a2a",
                routes=create_jsonrpc_routes(
                    request_handler=request_handler,
                    rpc_url="/jsonrpc",
                    context_builder=KeycardServerCallContextBuilder(),
                ),
                middleware=[
                    Middleware(
                        AuthenticationMiddleware,
                        backend=EagerKeycardAuthBackend(verifier),
                        on_error=keycard_on_error,
                    ),
                ],
            ),
        ]
    )


@pytest.fixture
def client(app):
    return TestClient(app)


class TestAgentCardEndpoint:
    """Tests for `/.well-known/agent-card.json` (a2a-sdk route factory)."""

    def test_get_agent_card_returns_200(self, client):
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200

    def test_agent_card_publicly_accessible(self, client):
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200

    def test_agent_card_carries_core_fields(self, client, service_config):
        response = client.get("/.well-known/agent-card.json")
        data = response.json()
        assert data["name"] == service_config.service_name
        assert data["version"]
        assert "capabilities" in data
        assert "skills" in data
        assert len(data["skills"]) == len(service_config.capabilities)

    def test_agent_card_supported_interfaces_includes_jsonrpc(
        self, client, service_config
    ):
        response = client.get("/.well-known/agent-card.json")
        data = response.json()
        # MessageToDict emits the protobuf field as camelCase.
        interfaces = (
            data.get("supportedInterfaces")
            or data.get("supported_interfaces")
            or []
        )
        assert any(
            iface.get("url", "").endswith("/a2a/jsonrpc") for iface in interfaces
        )


class TestJsonRpcAuthGate:
    """The `/a2a/jsonrpc` mount must reject anonymous requests with 401.

    A 401 means ``EagerKeycardAuthBackend`` caught the missing/malformed
    auth before the JSONRPC dispatcher saw the body. Any other status
    means the gate let the request through, which is a regression.
    """

    def test_jsonrpc_requires_authorization(self, client):
        response = client.post(
            "/a2a/jsonrpc",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "message/send",
                "params": {},
            },
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Bearer")

    def test_jsonrpc_rejects_malformed_authorization(self, client):
        response = client.post(
            "/a2a/jsonrpc",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "message/send",
                "params": {},
            },
            headers={"Authorization": "not-a-bearer-token"},
        )
        # Malformed scheme returns 400 per the underlying backend's
        # contract; the body is rejected before reaching the dispatcher.
        assert response.status_code in (400, 401)


class TestKeycardServerCallContextBuilder:
    """The context builder is the bridge between Keycard auth and a2a-sdk.

    It propagates the verified KeycardUser plus the bare access_token from
    the Starlette request into ``ServerCallContext.state`` where the
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

        ctx = KeycardServerCallContextBuilder().build(request)

        assert ctx.state.get("access_token") == "t-deadbeef"
        assert ctx.state.get("keycard_user") is user

    def test_builder_omits_access_token_for_non_keycard_user(self):
        # When the upstream middleware did not produce a KeycardUser, the
        # builder must not fabricate state entries: an executor reading
        # ``state["access_token"]`` then sees ``None`` rather than a token
        # from a different request.
        from starlette.authentication import UnauthenticatedUser

        request = self._make_request(user=UnauthenticatedUser())

        ctx = KeycardServerCallContextBuilder().build(request)

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
        # The endpoint proxies to the auth server which is unreachable in
        # tests; accept any non-error response shape.
        assert response.status_code in (200, 302, 307, 503)
