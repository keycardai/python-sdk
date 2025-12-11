"""Unit tests for metadata router functions.

These tests verify the route and mount creation for OAuth metadata endpoints.
"""

from unittest.mock import Mock

from starlette.routing import Mount, Route

from keycardai.mcp.server.routers.metadata import (
    auth_metadata_mount,
    protected_mcp_router,
    well_known_authorization_server_route,
    well_known_jwks_route,
    well_known_metadata_mount,
    well_known_metadata_routes,
    well_known_protected_resource_route,
)


class TestAuthMetadataMount:
    """Tests for auth_metadata_mount function."""

    def test_returns_mount_instance(self):
        """Test that the function returns a Starlette Mount."""
        issuer = "https://auth.example.com"
        result = auth_metadata_mount(issuer)
        assert isinstance(result, Mount)

    def test_mount_path_is_well_known(self):
        """Test that mount is at /.well-known path."""
        issuer = "https://auth.example.com"
        result = auth_metadata_mount(issuer)
        assert result.path == "/.well-known"

    def test_contains_protected_resource_route(self):
        """Test that mount contains protected resource route."""
        issuer = "https://auth.example.com"
        result = auth_metadata_mount(issuer)
        route_names = [r.name for r in result.routes if hasattr(r, "name")]
        assert "oauth-protected-resource" in route_names

    def test_contains_authorization_server_route(self):
        """Test that mount contains authorization server route."""
        issuer = "https://auth.example.com"
        result = auth_metadata_mount(issuer)
        route_names = [r.name for r in result.routes if hasattr(r, "name")]
        assert "oauth-authorization-server" in route_names

    def test_without_jwks_no_jwks_route(self):
        """Test that JWKS route is not included when jwks is None."""
        issuer = "https://auth.example.com"
        result = auth_metadata_mount(issuer, jwks=None)
        route_names = [r.name for r in result.routes if hasattr(r, "name")]
        assert "jwks" not in route_names

    def test_with_jwks_includes_jwks_route(self):
        """Test that JWKS route is included when jwks is provided."""
        issuer = "https://auth.example.com"
        jwks = {"keys": [{"kty": "RSA", "kid": "test-key"}]}
        result = auth_metadata_mount(issuer, jwks=jwks)
        route_names = [r.name for r in result.routes if hasattr(r, "name")]
        assert "jwks" in route_names

    def test_enable_multi_zone_parameter_passed(self):
        """Test that enable_multi_zone parameter is accepted."""
        issuer = "https://auth.example.com"
        # Should not raise
        result = auth_metadata_mount(issuer, enable_multi_zone=True)
        assert isinstance(result, Mount)


class TestWellKnownMetadataMount:
    """Tests for well_known_metadata_mount function."""

    def test_returns_mount_instance(self):
        """Test that the function returns a Starlette Mount."""
        issuer = "https://auth.example.com"
        result = well_known_metadata_mount(issuer, path="/custom-path")
        assert isinstance(result, Mount)

    def test_custom_path(self):
        """Test that mount uses the provided custom path."""
        issuer = "https://auth.example.com"
        custom_path = "/custom/.well-known"
        result = well_known_metadata_mount(issuer, path=custom_path)
        assert result.path == custom_path

    def test_default_resource_empty_string(self):
        """Test that default resource parameter is empty string."""
        issuer = "https://auth.example.com"
        result = well_known_metadata_mount(issuer, path="/.well-known")
        assert isinstance(result, Mount)
        # Routes should still be present
        assert len(result.routes) >= 2

    def test_with_resource_parameter(self):
        """Test with custom resource parameter."""
        issuer = "https://auth.example.com"
        result = well_known_metadata_mount(
            issuer, path="/.well-known", resource="/custom-resource"
        )
        assert isinstance(result, Mount)

    def test_with_all_parameters(self):
        """Test with all parameters provided."""
        issuer = "https://auth.example.com"
        jwks = {"keys": []}
        result = well_known_metadata_mount(
            issuer=issuer,
            path="/api/.well-known",
            resource="/resource",
            enable_multi_zone=True,
            jwks=jwks,
        )
        assert isinstance(result, Mount)
        assert result.path == "/api/.well-known"


class TestWellKnownMetadataRoutes:
    """Tests for well_known_metadata_routes function."""

    def test_returns_list_of_routes(self):
        """Test that the function returns a list of Route objects."""
        issuer = "https://auth.example.com"
        result = well_known_metadata_routes(issuer)
        assert isinstance(result, list)
        assert all(isinstance(r, Route) for r in result)

    def test_contains_two_routes_without_jwks(self):
        """Test that two routes are returned when no JWKS is provided."""
        issuer = "https://auth.example.com"
        result = well_known_metadata_routes(issuer)
        assert len(result) == 2

    def test_contains_three_routes_with_jwks(self):
        """Test that three routes are returned when JWKS is provided."""
        issuer = "https://auth.example.com"
        jwks = {"keys": []}
        result = well_known_metadata_routes(issuer, jwks=jwks)
        assert len(result) == 3

    def test_route_names(self):
        """Test that routes have correct names."""
        issuer = "https://auth.example.com"
        result = well_known_metadata_routes(issuer)
        route_names = {r.name for r in result}
        assert "oauth-protected-resource" in route_names
        assert "oauth-authorization-server" in route_names

    def test_jwks_route_name_when_provided(self):
        """Test that JWKS route has correct name when provided."""
        issuer = "https://auth.example.com"
        jwks = {"keys": []}
        result = well_known_metadata_routes(issuer, jwks=jwks)
        route_names = {r.name for r in result}
        assert "jwks" in route_names

    def test_enable_multi_zone_parameter(self):
        """Test that enable_multi_zone parameter is accepted."""
        issuer = "https://auth.example.com"
        result = well_known_metadata_routes(issuer, enable_multi_zone=True)
        assert len(result) == 2


class TestWellKnownProtectedResourceRoute:
    """Tests for well_known_protected_resource_route function."""

    def test_returns_route_instance(self):
        """Test that the function returns a Starlette Route."""
        issuer = "https://auth.example.com"
        result = well_known_protected_resource_route(issuer)
        assert isinstance(result, Route)

    def test_default_path(self):
        """Test that default path is /oauth-protected-resource."""
        issuer = "https://auth.example.com"
        result = well_known_protected_resource_route(issuer)
        assert result.path == "/oauth-protected-resource"

    def test_custom_path(self):
        """Test with custom resource path."""
        issuer = "https://auth.example.com"
        custom_path = "/custom-protected-resource"
        result = well_known_protected_resource_route(issuer, resource=custom_path)
        assert result.path == custom_path

    def test_route_name(self):
        """Test that route has correct name."""
        issuer = "https://auth.example.com"
        result = well_known_protected_resource_route(issuer)
        assert result.name == "oauth-protected-resource"

    def test_enable_multi_zone_parameter(self):
        """Test that enable_multi_zone parameter is accepted."""
        issuer = "https://auth.example.com"
        result = well_known_protected_resource_route(issuer, enable_multi_zone=True)
        assert isinstance(result, Route)

    def test_route_has_endpoint(self):
        """Test that route has an endpoint function."""
        issuer = "https://auth.example.com"
        result = well_known_protected_resource_route(issuer)
        assert result.endpoint is not None
        assert callable(result.endpoint)


class TestWellKnownAuthorizationServerRoute:
    """Tests for well_known_authorization_server_route function."""

    def test_returns_route_instance(self):
        """Test that the function returns a Starlette Route."""
        issuer = "https://auth.example.com"
        result = well_known_authorization_server_route(issuer)
        assert isinstance(result, Route)

    def test_default_path(self):
        """Test that default path is /oauth-authorization-server."""
        issuer = "https://auth.example.com"
        result = well_known_authorization_server_route(issuer)
        assert result.path == "/oauth-authorization-server"

    def test_custom_path(self):
        """Test with custom resource path."""
        issuer = "https://auth.example.com"
        custom_path = "/custom-auth-server"
        result = well_known_authorization_server_route(issuer, resource=custom_path)
        assert result.path == custom_path

    def test_route_name(self):
        """Test that route has correct name."""
        issuer = "https://auth.example.com"
        result = well_known_authorization_server_route(issuer)
        assert result.name == "oauth-authorization-server"

    def test_enable_multi_zone_parameter(self):
        """Test that enable_multi_zone parameter is accepted."""
        issuer = "https://auth.example.com"
        result = well_known_authorization_server_route(issuer, enable_multi_zone=True)
        assert isinstance(result, Route)

    def test_route_has_endpoint(self):
        """Test that route has an endpoint function."""
        issuer = "https://auth.example.com"
        result = well_known_authorization_server_route(issuer)
        assert result.endpoint is not None
        assert callable(result.endpoint)


class TestWellKnownJwksRoute:
    """Tests for well_known_jwks_route function."""

    def test_returns_route_instance(self):
        """Test that the function returns a Starlette Route."""
        jwks = {"keys": []}
        result = well_known_jwks_route(jwks)
        assert isinstance(result, Route)

    def test_path_is_jwks_json(self):
        """Test that path is /jwks.json."""
        jwks = {"keys": []}
        result = well_known_jwks_route(jwks)
        assert result.path == "/jwks.json"

    def test_route_name(self):
        """Test that route has correct name."""
        jwks = {"keys": []}
        result = well_known_jwks_route(jwks)
        assert result.name == "jwks"

    def test_route_has_endpoint(self):
        """Test that route has an endpoint function."""
        jwks = {"keys": []}
        result = well_known_jwks_route(jwks)
        assert result.endpoint is not None
        assert callable(result.endpoint)

    def test_with_populated_jwks(self):
        """Test with a populated JWKS."""
        jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": "test-key-1",
                    "use": "sig",
                    "n": "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6tSoc_BJECPebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0_FDW2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw",
                    "e": "AQAB",
                }
            ]
        }
        result = well_known_jwks_route(jwks)
        assert isinstance(result, Route)


class TestEdgeCases:
    """Test edge cases and parameter combinations."""

    def test_issuer_with_trailing_slash(self):
        """Test issuer URL with trailing slash."""
        issuer = "https://auth.example.com/"
        result = auth_metadata_mount(issuer)
        assert isinstance(result, Mount)

    def test_issuer_with_path(self):
        """Test issuer URL with path."""
        issuer = "https://auth.example.com/oauth"
        result = auth_metadata_mount(issuer)
        assert isinstance(result, Mount)

    def test_issuer_with_port(self):
        """Test issuer URL with port."""
        issuer = "https://auth.example.com:8443"
        result = auth_metadata_mount(issuer)
        assert isinstance(result, Mount)

    def test_http_issuer(self):
        """Test with HTTP issuer (development scenario)."""
        issuer = "http://localhost:8000"
        result = auth_metadata_mount(issuer)
        assert isinstance(result, Mount)

    def test_empty_jwks_keys(self):
        """Test with empty JWKS keys array."""
        issuer = "https://auth.example.com"
        jwks = {"keys": []}
        result = well_known_metadata_routes(issuer, jwks=jwks)
        assert len(result) == 3  # Should still include JWKS route

    def test_all_parameters_combined(self):
        """Test with all parameters provided."""
        mock_app = Mock()
        mock_verifier = Mock()
        jwks = {"keys": [{"kty": "RSA", "kid": "test"}]}

        result = protected_mcp_router(
            issuer="https://auth.example.com",
            mcp_app=mock_app,
            verifier=mock_verifier,
            enable_multi_zone=True,
            jwks=jwks,
        )

        assert len(result) == 2
        # Metadata mount with JWKS
        metadata_mount = result[0]
        route_names = [r.name for r in metadata_mount.routes if hasattr(r, "name")]
        assert "jwks" in route_names
        # Multi-zone MCP mount
        mcp_mount = result[1]
        assert mcp_mount.path == "/{zone_id:str}"
