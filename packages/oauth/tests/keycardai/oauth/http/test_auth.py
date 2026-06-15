"""Unit tests for OAuth 2.0 client authentication strategies."""

import base64

import pytest

from keycardai.oauth.http.auth import (
    BasicAuth,
    BearerAuth,
    MultiZoneBasicAuth,
    NoneAuth,
)


def _basic_header(client_id: str, client_secret: str) -> str:
    encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return f"Basic {encoded}"


class TestSingleCredentialStrategies:
    def test_none_auth_ignores_issuer_selector(self):
        auth = NoneAuth()
        assert auth.apply_headers() == {}
        assert auth.apply_headers("https://zone1.keycard.cloud") == {}

    def test_basic_auth_ignores_issuer_selector(self):
        auth = BasicAuth("client", "secret")
        expected = {"Authorization": _basic_header("client", "secret")}
        assert auth.apply_headers() == expected
        assert auth.apply_headers("https://zone1.keycard.cloud") == expected

    def test_bearer_auth_ignores_issuer_selector(self):
        auth = BearerAuth("token123")
        expected = {"Authorization": "Bearer token123"}
        assert auth.apply_headers() == expected
        assert auth.apply_headers("https://zone1.keycard.cloud") == expected


class TestMultiZoneBasicAuth:
    def make_auth(self) -> MultiZoneBasicAuth:
        return MultiZoneBasicAuth({
            "https://zone1.keycard.cloud": ("client_id_1", "client_secret_1"),
            "https://zone2.keycard.cloud": ("client_id_2", "client_secret_2"),
        })

    def test_apply_headers_selects_issuer_credentials(self):
        auth = self.make_auth()
        assert auth.apply_headers("https://zone1.keycard.cloud") == {
            "Authorization": _basic_header("client_id_1", "client_secret_1")
        }
        assert auth.apply_headers("https://zone2.keycard.cloud") == {
            "Authorization": _basic_header("client_id_2", "client_secret_2")
        }

    def test_apply_headers_without_issuer_raises(self):
        auth = self.make_auth()
        with pytest.raises(ValueError, match="requires an issuer"):
            auth.apply_headers()
        with pytest.raises(ValueError, match="requires an issuer"):
            auth.apply_headers(None)

    def test_apply_headers_unknown_issuer_fails_closed(self):
        auth = self.make_auth()
        with pytest.raises(KeyError, match="not configured"):
            auth.apply_headers("https://unknown.keycard.cloud")

    def test_trailing_slash_normalization(self):
        auth = MultiZoneBasicAuth({
            "https://zone1.keycard.cloud/": ("client_id_1", "client_secret_1"),
        })
        expected = {
            "Authorization": _basic_header("client_id_1", "client_secret_1")
        }
        assert auth.apply_headers("https://zone1.keycard.cloud") == expected
        assert auth.apply_headers("https://zone1.keycard.cloud/") == expected
        assert auth.has_issuer("https://zone1.keycard.cloud")
        assert auth.has_issuer("https://zone1.keycard.cloud/")
        assert auth.get_configured_issuers() == ["https://zone1.keycard.cloud"]

    def test_get_auth_for_issuer(self):
        auth = self.make_auth()
        basic = auth.get_auth_for_issuer("https://zone1.keycard.cloud")
        assert isinstance(basic, BasicAuth)
        assert basic.client_id == "client_id_1"
        with pytest.raises(KeyError, match="not configured"):
            auth.get_auth_for_issuer("https://unknown.keycard.cloud")

    def test_has_issuer(self):
        auth = self.make_auth()
        assert auth.has_issuer("https://zone1.keycard.cloud")
        assert not auth.has_issuer("https://unknown.keycard.cloud")

    def test_empty_credentials_rejected(self):
        with pytest.raises(ValueError):
            MultiZoneBasicAuth({})
        with pytest.raises(ValueError):
            MultiZoneBasicAuth({"https://zone1.keycard.cloud": ("", "secret")})
        with pytest.raises(ValueError):
            MultiZoneBasicAuth({"https://zone1.keycard.cloud": ("id", "")})
        with pytest.raises(ValueError):
            MultiZoneBasicAuth({"": ("id", "secret")})
