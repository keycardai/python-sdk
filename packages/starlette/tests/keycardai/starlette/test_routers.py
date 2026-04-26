"""Integration tests for OAuth metadata route builders.

Uses Starlette's TestClient to verify HTTP responses from the
RFC 9728 / RFC 8414 discovery endpoints.
"""

from unittest.mock import Mock, patch

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from keycardai.oauth.types import JsonWebKey, JsonWebKeySet
from keycardai.starlette.routers.metadata import (
    auth_metadata_mount,
    well_known_metadata_mount,
)


@pytest.fixture
def issuer():
    return "https://auth.localdev.keycard.sh"


@pytest.fixture
def app(issuer):
    return Starlette(
        routes=[well_known_metadata_mount(issuer=issuer, path="/.well-known")]
    )


@pytest.fixture
def client(app):
    return TestClient(app)


class TestProtectedResourceMetadata:
    def test_returns_200(self, client):
        response = client.get("/.well-known/oauth-protected-resource")
        assert response.status_code == 200

    def test_returns_application_json_content_type(self, client):
        response = client.get("/.well-known/oauth-protected-resource")
        assert response.headers["content-type"].startswith("application/json")

    def test_contains_authorization_servers(self, issuer, client):
        response = client.get("/.well-known/oauth-protected-resource")
        data = response.json()
        assert isinstance(data["authorization_servers"], list)
        assert len(data["authorization_servers"]) == 1
        assert f"{issuer}/" in data["authorization_servers"]

    def test_contains_resource_url(self, client):
        response = client.get("/.well-known/oauth-protected-resource")
        assert "testserver" in response.json()["resource"]

    def test_contains_jwks_uri(self, client):
        response = client.get("/.well-known/oauth-protected-resource")
        assert "/.well-known/jwks.json" in response.json()["jwks_uri"]

    def test_contains_client_id(self, client):
        data = client.get("/.well-known/oauth-protected-resource").json()
        assert data["client_id"] == data["resource"]

    def test_contains_grant_types(self, client):
        data = client.get("/.well-known/oauth-protected-resource").json()
        assert "client_credentials" in data["grant_types"]


class TestAuthorizationServerMetadata:
    def test_proxies_upstream(self, client, issuer):
        upstream = {"issuer": issuer, "token_endpoint": f"{issuer}/oauth/token"}
        with patch("httpx.Client") as mock_client_cls:
            mock_resp = Mock()
            mock_resp.json.return_value = upstream
            mock_resp.raise_for_status.return_value = None
            mock_client_cls.return_value.__enter__.return_value.get.return_value = (
                mock_resp
            )
            response = client.get("/.well-known/oauth-authorization-server")
            assert response.status_code == 200
            assert response.json()["issuer"] == issuer

    def test_upstream_503_on_connect_error(self, client):
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.side_effect = (
                httpx.ConnectError("connection refused")
            )
            response = client.get("/.well-known/oauth-authorization-server")
            assert response.status_code == 503

    def test_explicit_timeout_passed_to_client(self, client, issuer):
        """authorization_server_metadata must pass an explicit timeout to httpx."""
        with patch("httpx.Client") as mock_client_cls:
            mock_resp = Mock()
            mock_resp.json.return_value = {"issuer": issuer}
            mock_resp.raise_for_status.return_value = None
            mock_client_cls.return_value.__enter__.return_value.get.return_value = (
                mock_resp
            )
            client.get("/.well-known/oauth-authorization-server")
            call_kwargs = mock_client_cls.call_args.kwargs
            assert "timeout" in call_kwargs, (
                "httpx.Client must be constructed with explicit timeout to "
                "avoid pinning a threadpool worker indefinitely."
            )


class TestJwksEndpoint:
    def test_returns_jwks_when_provided(self, issuer):
        jwks = JsonWebKeySet(
            keys=[
                JsonWebKey(
                    kty="RSA",
                    kid="test-key-1",
                    use="sig",
                    alg="RS256",
                    n="test-modulus",
                    e="AQAB",
                )
            ]
        )
        app = Starlette(
            routes=[
                well_known_metadata_mount(
                    issuer=issuer, path="/.well-known", jwks=jwks
                )
            ]
        )
        response = TestClient(app).get("/.well-known/jwks.json")
        assert response.status_code == 200
        assert response.json()["keys"][0]["kid"] == "test-key-1"

    def test_omitted_when_no_jwks(self, client):
        assert client.get("/.well-known/jwks.json").status_code == 404


class TestAuthMetadataMount:
    def test_mounts_at_well_known(self, issuer):
        app = Starlette(routes=[auth_metadata_mount(issuer=issuer)])
        response = TestClient(app).get(
            "/.well-known/oauth-protected-resource/any/path"
        )
        assert response.status_code == 200
