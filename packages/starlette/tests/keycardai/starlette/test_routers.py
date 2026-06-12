"""Integration tests for OAuth metadata route builders.

Uses Starlette's TestClient to verify HTTP responses from the
RFC 9728 / RFC 8414 discovery endpoints.
"""

from unittest.mock import Mock, patch

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from keycardai.oauth.server.verifier import TokenVerifier
from keycardai.oauth.types import JsonWebKey, JsonWebKeySet
from keycardai.starlette.routers.metadata import (
    auth_metadata_mount,
    protected_router,
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


@pytest.fixture
def sample_jwks():
    return JsonWebKeySet(
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

    def test_contains_jwks_uri_when_jwks_supplied(self, issuer, sample_jwks):
        app = Starlette(
            routes=[
                well_known_metadata_mount(
                    issuer=issuer, path="/.well-known", jwks=sample_jwks
                )
            ]
        )
        response = TestClient(app).get("/.well-known/oauth-protected-resource")
        assert "/.well-known/jwks.json" in response.json()["jwks_uri"]

    def test_omits_jwks_uri_without_jwks(self, client):
        data = client.get("/.well-known/oauth-protected-resource").json()
        assert "jwks_uri" not in data

    def test_omits_client_registration_fields(self, client):
        data = client.get("/.well-known/oauth-protected-resource").json()
        for field in (
            "client_id",
            "client_name",
            "token_endpoint_auth_method",
            "grant_types",
        ):
            assert field not in data

    def test_contains_bearer_methods_supported(self, client):
        data = client.get("/.well-known/oauth-protected-resource").json()
        assert data["bearer_methods_supported"] == ["header"]

    def test_optional_resource_fields_absent_by_default(self, client):
        data = client.get("/.well-known/oauth-protected-resource").json()
        for field in ("scopes_supported", "resource_name", "resource_documentation"):
            assert field not in data

    def test_optional_resource_fields_emitted_when_supplied(self, issuer):
        app = Starlette(
            routes=[
                well_known_metadata_mount(
                    issuer=issuer,
                    path="/.well-known",
                    scopes_supported=["read", "write"],
                    resource_name="Example Resource",
                    resource_documentation="https://docs.example.com/",
                )
            ]
        )
        data = (
            TestClient(app).get("/.well-known/oauth-protected-resource").json()
        )
        assert data["scopes_supported"] == ["read", "write"]
        assert data["resource_name"] == "Example Resource"
        assert data["resource_documentation"] == "https://docs.example.com/"

    def test_get_has_cors_header(self, client):
        response = client.get("/.well-known/oauth-protected-resource")
        assert response.headers["access-control-allow-origin"] == "*"

    def test_options_preflight(self, client):
        response = client.options("/.well-known/oauth-protected-resource")
        assert response.status_code == 204
        assert response.headers["access-control-allow-origin"] == "*"
        assert response.headers["access-control-allow-methods"] == "GET, OPTIONS"
        assert (
            response.headers["access-control-allow-headers"]
            == "Content-Type, MCP-Protocol-Version"
        )


def _mock_upstream(mock_client_cls, payload):
    mock_resp = Mock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None
    mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp


class TestAuthorizationServerMetadata:
    def test_proxies_upstream(self, client, issuer):
        upstream = {"issuer": issuer, "token_endpoint": f"{issuer}/oauth/token"}
        with patch("httpx.Client") as mock_client_cls:
            _mock_upstream(mock_client_cls, upstream)
            response = client.get("/.well-known/oauth-authorization-server")
            assert response.status_code == 200
            assert response.json()["issuer"] == issuer

    def test_upstream_502_on_connect_error(self, client):
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.get.side_effect = (
                httpx.ConnectError("connection refused")
            )
            response = client.get("/.well-known/oauth-authorization-server")
            assert response.status_code == 502

    def test_upstream_502_on_upstream_http_error(self, client):
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_response = Mock()
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "server error",
                request=httpx.Request(
                    "GET", "https://zone.example.com/.well-known/oauth-authorization-server"
                ),
                response=httpx.Response(
                    500,
                    request=httpx.Request(
                        "GET",
                        "https://zone.example.com/.well-known/oauth-authorization-server",
                    ),
                ),
            )
            mock_client_cls.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )
            response = client.get("/.well-known/oauth-authorization-server")
            assert response.status_code == 502

    def test_authorization_endpoint_gains_resource_param(self, client, issuer):
        upstream = {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth/authorize",
        }
        with patch("httpx.Client") as mock_client_cls:
            _mock_upstream(mock_client_cls, upstream)
            data = client.get("/.well-known/oauth-authorization-server").json()
            assert (
                data["authorization_endpoint"]
                == f"{issuer}/oauth/authorize?resource=http%3A%2F%2Ftestserver"
            )

    def test_authorization_endpoint_preserves_existing_query(self, client, issuer):
        upstream = {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth/authorize?audience=abc",
        }
        with patch("httpx.Client") as mock_client_cls:
            _mock_upstream(mock_client_cls, upstream)
            data = client.get("/.well-known/oauth-authorization-server").json()
            assert (
                data["authorization_endpoint"]
                == f"{issuer}/oauth/authorize?audience=abc&resource=http%3A%2F%2Ftestserver"
            )

    def test_no_authorization_endpoint_left_unchanged(self, client, issuer):
        upstream = {"issuer": issuer}
        with patch("httpx.Client") as mock_client_cls:
            _mock_upstream(mock_client_cls, upstream)
            data = client.get("/.well-known/oauth-authorization-server").json()
            assert "authorization_endpoint" not in data

    def test_default_timeout_passed_to_client(self, client, issuer):
        with patch("httpx.Client") as mock_client_cls:
            _mock_upstream(mock_client_cls, {"issuer": issuer})
            client.get("/.well-known/oauth-authorization-server")
            call_kwargs = mock_client_cls.call_args.kwargs
            assert "timeout" in call_kwargs, (
                "httpx.Client must be constructed with explicit timeout to "
                "avoid pinning a threadpool worker indefinitely."
            )
            assert call_kwargs["timeout"] == httpx.Timeout(10.0)

    def test_custom_timeout_passed_to_client(self, issuer):
        app = Starlette(
            routes=[
                well_known_metadata_mount(
                    issuer=issuer, path="/.well-known", as_metadata_timeout=2.5
                )
            ]
        )
        with patch("httpx.Client") as mock_client_cls:
            _mock_upstream(mock_client_cls, {"issuer": issuer})
            TestClient(app).get("/.well-known/oauth-authorization-server")
            assert mock_client_cls.call_args.kwargs["timeout"] == httpx.Timeout(2.5)

    def test_get_has_cors_header(self, client, issuer):
        with patch("httpx.Client") as mock_client_cls:
            _mock_upstream(mock_client_cls, {"issuer": issuer})
            response = client.get("/.well-known/oauth-authorization-server")
            assert response.headers["access-control-allow-origin"] == "*"

    def test_options_preflight(self, client):
        response = client.options("/.well-known/oauth-authorization-server")
        assert response.status_code == 204
        assert response.headers["access-control-allow-origin"] == "*"
        assert response.headers["access-control-allow-methods"] == "GET, OPTIONS"
        assert (
            response.headers["access-control-allow-headers"]
            == "Content-Type, MCP-Protocol-Version"
        )


class TestJwksEndpoint:
    def _client(self, issuer, jwks):
        app = Starlette(
            routes=[
                well_known_metadata_mount(
                    issuer=issuer, path="/.well-known", jwks=jwks
                )
            ]
        )
        return TestClient(app)

    def test_returns_jwks_when_provided(self, issuer, sample_jwks):
        response = self._client(issuer, sample_jwks).get("/.well-known/jwks.json")
        assert response.status_code == 200
        assert response.json()["keys"][0]["kid"] == "test-key-1"

    def test_get_has_cors_header(self, issuer, sample_jwks):
        response = self._client(issuer, sample_jwks).get("/.well-known/jwks.json")
        assert response.headers["access-control-allow-origin"] == "*"

    def test_options_preflight(self, issuer, sample_jwks):
        response = self._client(issuer, sample_jwks).options(
            "/.well-known/jwks.json"
        )
        assert response.status_code == 204
        assert response.headers["access-control-allow-origin"] == "*"
        assert response.headers["access-control-allow-methods"] == "GET, OPTIONS"
        assert (
            response.headers["access-control-allow-headers"]
            == "Content-Type, MCP-Protocol-Version"
        )

    def test_omitted_when_no_jwks(self, client):
        assert client.get("/.well-known/jwks.json").status_code == 404


class TestAuthMetadataMount:
    def test_mounts_at_well_known(self, issuer):
        app = Starlette(routes=[auth_metadata_mount(issuer=issuer)])
        response = TestClient(app).get(
            "/.well-known/oauth-protected-resource/any/path"
        )
        assert response.status_code == 200


class TestProtectedRouterRequireAuthentication:
    """Tokenless requests on an opaque mounted app must be challenged when
    ``require_authentication=True`` (the contract used by protected_mcp_router).
    """

    async def _ok_app(self, scope, receive, send):
        await PlainTextResponse("ok")(scope, receive, send)

    def _client(self, issuer, *, require_authentication):
        verifier = TokenVerifier(issuer=issuer)
        app = Starlette(
            routes=protected_router(
                issuer=issuer,
                app=self._ok_app,
                verifier=verifier,
                require_authentication=require_authentication,
            )
        )
        return TestClient(app)

    def test_tokenless_request_is_challenged_when_required(self, issuer):
        response = self._client(issuer, require_authentication=True).get("/anything")
        assert response.status_code == 401
        assert "WWW-Authenticate" in response.headers
        assert response.headers["WWW-Authenticate"].startswith("Bearer")
        assert "resource_metadata=" in response.headers["WWW-Authenticate"]

    def test_tokenless_request_passes_through_by_default(self, issuer):
        response = self._client(issuer, require_authentication=False).get("/anything")
        assert response.status_code == 200
        assert response.text == "ok"

    def test_oauth_metadata_remains_public_when_required(self, issuer):
        response = self._client(issuer, require_authentication=True).get(
            "/.well-known/oauth-protected-resource"
        )
        assert response.status_code == 200
