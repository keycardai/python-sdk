"""Tests for OAuth 2.0 authentication strategies."""

import pytest

from keycardai.oauth.client.auth import (
    AuthStrategy,
    ClientCredentialsAuth,
    ClientSecretBasic,
    ClientSecretPost,
    JWTAuth,
    MTLSAuth,
    NoneAuth,
)


class TestAuthStrategy:
    """Test AuthStrategy protocol."""

    def test_protocol_exists(self):
        """Test that AuthStrategy protocol is properly defined."""
        assert hasattr(AuthStrategy, "__annotations__")


class TestClientCredentialsAuth:
    """Test ClientCredentialsAuth implementation."""

    def test_create(self):
        """Test creating ClientCredentialsAuth."""
        auth = ClientCredentialsAuth("client_id", "client_secret")

        assert auth.client_id == "client_id"
        assert auth.client_secret == "client_secret"

    def test_get_basic_auth(self):
        """Test getting basic authentication credentials."""
        auth = ClientCredentialsAuth("client_id", "client_secret", method="basic")

        basic_auth = auth.get_basic_auth()

        # Should return basic auth tuple
        assert basic_auth == ("client_id", "client_secret")

    def test_get_auth_data_post(self):
        """Test getting authentication data for post method."""
        auth = ClientCredentialsAuth("client_id", "client_secret", method="post")

        auth_data = auth.get_auth_data()

        # Should return client credentials in data
        assert auth_data["client_id"] == "client_id"
        assert auth_data["client_secret"] == "client_secret"

    def test_default_method(self):
        """Test default authentication method is basic."""
        auth = ClientCredentialsAuth("client_id", "client_secret")

        basic_auth = auth.get_basic_auth()
        auth_data = auth.get_auth_data()

        # Should use basic auth by default
        assert basic_auth == ("client_id", "client_secret")
        assert auth_data == {}  # No form data for basic auth


class TestClientSecretBasic:
    """Test ClientSecretBasic factory function."""

    def test_create(self):
        """Test creating ClientSecretBasic auth."""
        auth = ClientSecretBasic("client_id", "client_secret")

        assert isinstance(auth, ClientCredentialsAuth)
        assert auth.client_id == "client_id"
        assert auth.client_secret == "client_secret"

    def test_provides_basic_auth(self):
        """Test that it provides basic authentication."""
        auth = ClientSecretBasic("client_id", "client_secret")

        basic_auth = auth.get_basic_auth()

        # Should return basic auth tuple
        assert basic_auth == ("client_id", "client_secret")


class TestClientSecretPost:
    """Test ClientSecretPost factory function."""

    def test_create(self):
        """Test creating ClientSecretPost auth."""
        auth = ClientSecretPost("client_id", "client_secret")

        assert isinstance(auth, ClientCredentialsAuth)
        assert auth.client_id == "client_id"
        assert auth.client_secret == "client_secret"

    def test_provides_post_auth(self):
        """Test that it provides post authentication."""
        auth = ClientSecretPost("client_id", "client_secret")

        auth_data = auth.get_auth_data()

        # Should return client credentials in data
        assert auth_data["client_id"] == "client_id"
        assert auth_data["client_secret"] == "client_secret"


class TestJWTAuth:
    """Test JWTAuth implementation."""

    def test_create(self):
        """Test creating JWTAuth."""
        auth = JWTAuth("client_id", "private_key", "RS256")

        assert auth.client_id == "client_id"
        assert auth.private_key == "private_key"
        assert auth.algorithm == "RS256"

    def test_get_auth_data_not_implemented(self):
        """Test that get_auth_data raises NotImplementedError."""
        auth = JWTAuth("client_id", "private_key", "RS256")

        with pytest.raises(
            NotImplementedError,
            match="JWT client assertion generation not yet implemented",
        ):
            auth.get_auth_data()


class TestMTLSAuth:
    """Test MTLSAuth implementation."""

    def test_create(self):
        """Test creating MTLSAuth."""
        auth = MTLSAuth("client_id", "cert_path", "key_path")

        assert auth.client_id == "client_id"
        assert auth.cert_path == "cert_path"
        assert auth.key_path == "key_path"

    def test_get_auth_data(self):
        """Test that get_auth_data returns client_id."""
        auth = MTLSAuth("client_id", "cert_path", "key_path")

        auth_data = auth.get_auth_data()

        # Should return only client_id for mTLS
        assert auth_data == {"client_id": "client_id"}


class TestNoneAuth:
    """Test NoneAuth implementation."""

    def test_create(self):
        """Test creating NoneAuth."""
        auth = NoneAuth()

        assert isinstance(auth, NoneAuth)

    def test_get_auth_data_returns_empty(self):
        """Test that get_auth_data returns empty dict."""
        auth = NoneAuth()

        auth_data = auth.get_auth_data()

        # Should return empty dict
        assert auth_data == {}

    def test_get_basic_auth_returns_none(self):
        """Test that get_basic_auth returns None."""
        auth = NoneAuth()

        basic_auth = auth.get_basic_auth()

        # Should return None
        assert basic_auth is None


class TestAuthStrategiesIntegration:
    """Test authentication strategies integration."""

    def test_all_auth_strategies_importable(self):
        """Test that all auth strategies can be imported together."""
        from keycardai.oauth.client.auth import (
            ClientCredentialsAuth,
            ClientSecretBasic,
            ClientSecretPost,
            JWTAuth,
            MTLSAuth,
            NoneAuth,
        )

        # Should be able to create instances
        client_creds = ClientCredentialsAuth("client", "secret")
        basic_auth = ClientSecretBasic("client", "secret")
        post_auth = ClientSecretPost("client", "secret")
        jwt_auth = JWTAuth("client", "key", "RS256")
        mtls_auth = MTLSAuth("client", "cert", "key")
        none_auth = NoneAuth()

        assert isinstance(client_creds, ClientCredentialsAuth)
        assert isinstance(basic_auth, ClientCredentialsAuth)
        assert isinstance(post_auth, ClientCredentialsAuth)
        assert isinstance(jwt_auth, JWTAuth)
        assert isinstance(mtls_auth, MTLSAuth)
        assert isinstance(none_auth, NoneAuth)

    def test_auth_strategy_protocol_conformance(self):
        """Test that all auth strategies conform to AuthStrategy protocol."""
        strategies = [
            ClientCredentialsAuth("client", "secret"),
            JWTAuth("client", "key", "RS256"),
            MTLSAuth("client", "cert", "key"),
            NoneAuth(),
        ]

        for strategy in strategies:
            # All should have required methods
            assert hasattr(strategy, "authenticate")
            assert callable(strategy.authenticate)
            assert hasattr(strategy, "get_auth_data")
            assert callable(strategy.get_auth_data)
            assert hasattr(strategy, "get_basic_auth")
            assert callable(strategy.get_basic_auth)

    def test_factory_functions_create_correct_types(self):
        """Test that factory functions create the correct auth types."""
        basic = ClientSecretBasic("client", "secret")
        post = ClientSecretPost("client", "secret")

        # Both should be ClientCredentialsAuth instances
        assert isinstance(basic, ClientCredentialsAuth)
        assert isinstance(post, ClientCredentialsAuth)

        # But they should have different behavior
        basic_auth = basic.get_basic_auth()
        basic_data = basic.get_auth_data()
        post_auth = post.get_basic_auth()
        post_data = post.get_auth_data()

        # Basic should provide basic auth, post should provide form data
        assert basic_auth == ("client", "secret")
        assert basic_data == {}
        assert post_auth is None
        assert post_data == {"client_id": "client", "client_secret": "secret"}
