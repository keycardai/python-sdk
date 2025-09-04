"""Tests for OAuth 2.0 constants, enums, and well-known endpoints."""


from keycardai.oauth.types.oauth import (
    WellKnownEndpoint,
)


class TestConstructUrl:
    """Test WellKnownEndpoint.construct_url() method."""

    def test_construct_url_basic(self):
        """Test basic URL construction."""
        base_url = "https://example.com"
        expected_url = "https://example.com/.well-known/oauth-authorization-server"
        assert WellKnownEndpoint.construct_url(base_url, WellKnownEndpoint.OAUTH_AUTHORIZATION_SERVER) == expected_url

    def test_construct_url_with_trailing_slash(self):
        """Test URL construction with trailing slash on base URL."""
        base_url = "https://example.com/"
        expected_url = "https://example.com/.well-known/oauth-authorization-server"
        assert WellKnownEndpoint.construct_url(base_url, WellKnownEndpoint.OAUTH_AUTHORIZATION_SERVER) == expected_url

    def test_construct_url_different_endpoints(self):
        """Test URL construction with different well-known endpoints."""
        base_url = "https://auth.example.com"

        oauth_url = WellKnownEndpoint.construct_url(base_url, WellKnownEndpoint.OAUTH_AUTHORIZATION_SERVER)
        assert oauth_url == "https://auth.example.com/.well-known/oauth-authorization-server"

        openid_url = WellKnownEndpoint.construct_url(base_url, WellKnownEndpoint.OPENID_CONFIGURATION)
        assert openid_url == "https://auth.example.com/.well-known/openid-configuration"

        jwks_url = WellKnownEndpoint.construct_url(base_url, WellKnownEndpoint.JWKS)
        assert jwks_url == "https://auth.example.com/.well-known/jwks.json"

    def test_construct_url_complex_base_url(self):
        """Test URL construction with complex base URL."""
        base_url = "https://auth.example.com:8080/oauth/v1"
        expected_url = "https://auth.example.com:8080/oauth/v1/.well-known/oauth-authorization-server"
        assert WellKnownEndpoint.construct_url(base_url, WellKnownEndpoint.OAUTH_AUTHORIZATION_SERVER) == expected_url
