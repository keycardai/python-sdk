"""Integration tests for metadata router endpoints.

These tests use Starlette's TestClient to verify actual HTTP responses
from the OAuth metadata endpoints.
"""

from unittest.mock import Mock, patch

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from keycardai.mcp.server.routers.metadata import (
    auth_metadata_mount,
    well_known_metadata_mount,
)
from keycardai.oauth.types import JsonWebKey, JsonWebKeySet


class TestProtectedResourceMetadata:
    """Integration tests for protected resource metadata endpoint."""

    @pytest.fixture
    def issuer(self):
        return "https://auth.localdev.keycard.sh"

    @pytest.fixture
    def app(self, issuer):
        return Starlette(
            routes=[
                well_known_metadata_mount(issuer=issuer, path="/.well-known"),
            ]
        )

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_returns_200(self, client):
        """Test that protected resource endpoint returns 200 OK."""
        response = client.get("/.well-known/oauth-protected-resource")
        assert response.status_code == 200

    def test_contains_authorization_servers(self, issuer, client):
        """Test that response contains authorization_servers with issuer."""
        response = client.get("/.well-known/oauth-protected-resource")
        data = response.json()

        assert "authorization_servers" in data
        assert isinstance(data["authorization_servers"], list)
        assert len(data["authorization_servers"]) == 1
        assert f"{issuer}/" in data["authorization_servers"]

    def test_contains_resource_url(self, client):
        """Test that response contains resource field derived from request."""
        response = client.get("/.well-known/oauth-protected-resource")
        data = response.json()

        assert "resource" in data
        assert "testserver" in data["resource"]

    def test_contains_jwks_uri(self, client):
        """Test that response contains jwks_uri field."""
        response = client.get("/.well-known/oauth-protected-resource")
        data = response.json()

        assert "jwks_uri" in data
        assert "/.well-known/jwks.json" in data["jwks_uri"]

    def test_contains_client_id(self, client):
        """Test that response contains client_id matching resource."""
        response = client.get("/.well-known/oauth-protected-resource")
        data = response.json()

        assert "client_id" in data
        assert data["client_id"] == data["resource"]

    def test_contains_grant_types(self, client):
        """Test that response contains grant_types with client_credentials."""
        response = client.get("/.well-known/oauth-protected-resource")
        data = response.json()

        assert "grant_types" in data
        assert "client_credentials" in data["grant_types"]

    def test_contains_token_endpoint_auth_method(self, client):
        """Test that token_endpoint_auth_method is private_key_jwt."""
        response = client.get("/.well-known/oauth-protected-resource")
        data = response.json()

        assert "token_endpoint_auth_method" in data
        assert data["token_endpoint_auth_method"] == "private_key_jwt"


class TestAuthorizationServerMetadata:
    """Integration tests for authorization server metadata endpoint."""

    @pytest.fixture
    def issuer(self):
        return "https://auth.localdev.keycard.sh"

    @pytest.fixture
    def mock_upstream_response(self, issuer):
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth/authorize",
            "token_endpoint": f"{issuer}/oauth/token",
            "jwks_uri": f"{issuer}/.well-known/jwks.json",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "client_credentials"],
        }

    @pytest.fixture
    def app(self, issuer):
        return Starlette(
            routes=[
                well_known_metadata_mount(issuer=issuer, path="/.well-known"),
            ]
        )

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    @patch("httpx.Client")
    def test_returns_200_on_success(
        self, mock_client_class, client, mock_upstream_response
    ):
        """Test that endpoint returns 200 when upstream responds successfully."""
        mock_response = Mock()
        mock_response.json.return_value = mock_upstream_response
        mock_response.raise_for_status.return_value = None

        mock_client = Mock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value.__enter__.return_value = mock_client

        response = client.get("/.well-known/oauth-authorization-server")
        assert response.status_code == 200

    @patch("httpx.Client")
    def test_proxies_upstream_metadata(
        self, mock_client_class, client, issuer, mock_upstream_response
    ):
        """Test that endpoint returns metadata from upstream server."""
        mock_response = Mock()
        mock_response.json.return_value = mock_upstream_response
        mock_response.raise_for_status.return_value = None

        mock_client = Mock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value.__enter__.return_value = mock_client

        response = client.get("/.well-known/oauth-authorization-server")
        data = response.json()

        assert data["issuer"] == issuer
        assert data["authorization_endpoint"] == f"{issuer}/oauth/authorize"
        assert data["token_endpoint"] == f"{issuer}/oauth/token"

    @patch("httpx.Client")
    def test_preserves_all_upstream_fields(
        self, mock_client_class, client, mock_upstream_response
    ):
        """Test that all metadata fields from upstream are preserved."""
        mock_response = Mock()
        mock_response.json.return_value = mock_upstream_response
        mock_response.raise_for_status.return_value = None

        mock_client = Mock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value.__enter__.return_value = mock_client

        response = client.get("/.well-known/oauth-authorization-server")
        data = response.json()

        assert "jwks_uri" in data
        assert "response_types_supported" in data
        assert "grant_types_supported" in data


class TestJwksEndpoint:
    """Integration tests for JWKS endpoint."""

    @pytest.fixture
    def issuer(self):
        return "https://auth.localdev.keycard.sh"

    @pytest.fixture
    def jwks(self):
        return JsonWebKeySet(
            keys=[
                JsonWebKey(
                    kty="RSA",
                    kid="test-key-1",
                    use="sig",
                    n="0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6tSoc_BJECPebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0_FDW2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw",
                    e="AQAB",
                )
            ]
        )

    @pytest.fixture
    def app(self, issuer, jwks):
        return Starlette(
            routes=[
                well_known_metadata_mount(
                    issuer=issuer, path="/.well-known", jwks=jwks
                ),
            ]
        )

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_returns_200(self, client):
        """Test that JWKS endpoint returns 200 OK."""
        response = client.get("/.well-known/jwks.json")
        assert response.status_code == 200

    def test_returns_json_content_type(self, client):
        """Test that response has JSON content type."""
        response = client.get("/.well-known/jwks.json")
        assert response.headers["content-type"] == "application/json"

    def test_contains_keys_array(self, client):
        """Test that response contains keys array."""
        response = client.get("/.well-known/jwks.json")
        data = response.json()

        assert "keys" in data
        assert isinstance(data["keys"], list)
        assert len(data["keys"]) == 1

    def test_key_has_required_fields(self, client):
        """Test that key contains required JWK fields."""
        response = client.get("/.well-known/jwks.json")
        data = response.json()
        key = data["keys"][0]

        assert key["kty"] == "RSA"
        assert key["kid"] == "test-key-1"
        assert key["use"] == "sig"

    def test_rsa_key_has_public_components(self, client):
        """Test that RSA key has n and e components."""
        response = client.get("/.well-known/jwks.json")
        data = response.json()
        key = data["keys"][0]

        assert "n" in key
        assert "e" in key
        assert key["e"] == "AQAB"


class TestJwksEndpointEmpty:
    """Test JWKS endpoint with empty keys."""

    @pytest.fixture
    def app(self):
        return Starlette(
            routes=[
                well_known_metadata_mount(
                    issuer="https://auth.localdev.keycard.sh",
                    path="/.well-known",
                    jwks=JsonWebKeySet(keys=[]),
                ),
            ]
        )

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_returns_empty_keys_array(self, client):
        """Test that empty JWKS returns empty keys array."""
        response = client.get("/.well-known/jwks.json")

        assert response.status_code == 200
        data = response.json()
        assert data["keys"] == []


class TestAuthMetadataMount:
    """Integration tests for auth_metadata_mount convenience function."""

    @pytest.fixture
    def issuer(self):
        return "https://auth.localdev.keycard.sh"

    @pytest.fixture
    def jwks(self):
        return JsonWebKeySet(
            keys=[
                JsonWebKey(
                    kty="RSA",
                    kid="mount-test-key",
                    use="sig",
                    n="test-modulus",
                    e="AQAB",
                )
            ]
        )

    @pytest.fixture
    def app(self, issuer, jwks):
        mount = auth_metadata_mount(issuer=issuer, jwks=jwks)
        return Starlette(routes=[mount])

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_protected_resource_accessible(self, client):
        """Test that protected resource endpoint is accessible via mount."""
        response = client.get("/.well-known/oauth-protected-resource")
        assert response.status_code == 200

    def test_jwks_accessible(self, client):
        """Test that JWKS endpoint is accessible via mount."""
        response = client.get("/.well-known/jwks.json")
        assert response.status_code == 200

    def test_jwks_returns_configured_key(self, client):
        """Test that JWKS returns the configured key."""
        response = client.get("/.well-known/jwks.json")
        data = response.json()

        assert data["keys"][0]["kid"] == "mount-test-key"

    def test_protected_resource_has_correct_issuer(self, issuer, client):
        """Test that protected resource points to correct authorization server."""
        response = client.get("/.well-known/oauth-protected-resource")
        data = response.json()

        assert f"{issuer}/" in data["authorization_servers"]


class TestMultiZone:
    """Integration tests for multi-zone functionality."""

    @pytest.fixture
    def issuer(self):
        return "https://keycard.cloud"

    @pytest.fixture
    def app(self, issuer):
        mount = auth_metadata_mount(issuer=issuer, enable_multi_zone=True)
        return Starlette(routes=[mount])

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_without_zone_uses_base_issuer(self, issuer, client):
        """Test that request without zone ID uses base issuer."""
        response = client.get("/.well-known/oauth-protected-resource")

        assert response.status_code == 200
        data = response.json()
        assert f"{issuer}/" in data["authorization_servers"]
