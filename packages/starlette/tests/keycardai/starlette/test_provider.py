"""Tests for AuthProvider construction and install() wiring."""

import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.testclient import TestClient

from keycardai.oauth.server.credentials import ClientSecret
from keycardai.oauth.server.exceptions import AuthProviderConfigurationError
from keycardai.starlette import AuthProvider, BearerAuthMiddleware


class TestAuthProviderConstruction:
    def test_zone_id_derives_zone_url(self):
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )
        assert provider.zone_url == "https://test-zone.keycard.cloud"
        assert provider.issuer == "https://test-zone.keycard.cloud"

    def test_explicit_zone_url_wins(self):
        provider = AuthProvider(
            zone_url="https://custom.example.com",
            application_credential=ClientSecret(("cid", "csec")),
        )
        assert provider.zone_url == "https://custom.example.com"

    def test_missing_zone_raises(self):
        with pytest.raises(AuthProviderConfigurationError):
            AuthProvider()

    def test_multi_zone_allows_missing_zone_id(self):
        provider = AuthProvider(enable_multi_zone=True)
        assert provider.enable_multi_zone is True

    def test_zone_id_from_env(self, monkeypatch):
        monkeypatch.setenv("KEYCARD_ZONE_ID", "env-zone")
        provider = AuthProvider(
            application_credential=ClientSecret(("cid", "csec"))
        )
        assert "env-zone.keycard.cloud" in provider.zone_url

    def test_client_credentials_from_env(self, monkeypatch):
        monkeypatch.setenv("KEYCARD_CLIENT_ID", "cid")
        monkeypatch.setenv("KEYCARD_CLIENT_SECRET", "csec")
        provider = AuthProvider(zone_id="test-zone")
        assert isinstance(provider.application_credential, ClientSecret)


class TestAuthProviderInstall:
    @pytest.fixture
    def provider(self):
        return AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )

    def test_install_on_fastapi_adds_middleware(self, provider):
        app = FastAPI()
        provider.install(app)
        middleware_classes = [m.cls for m in app.user_middleware]
        assert BearerAuthMiddleware in middleware_classes

    def test_install_on_fastapi_adds_metadata_routes(self, provider):
        app = FastAPI()
        provider.install(app)
        paths = [
            getattr(r, "path", None) or getattr(r, "path_format", None)
            for r in app.routes
        ]
        assert any(
            p and "/.well-known" in p for p in paths
        ), f"No /.well-known route found in {paths}"

    def test_install_on_starlette_serves_protected_resource_metadata(
        self, provider
    ):
        app = Starlette()
        provider.install(app)
        response = TestClient(app).get("/.well-known/oauth-protected-resource")
        assert response.status_code == 200
        data = response.json()
        assert "authorization_servers" in data
        assert "test-zone.keycard.cloud" in data["authorization_servers"][0]

    def test_install_rejects_requests_without_bearer_token(self, provider):
        app = Starlette()
        provider.install(app)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/some/protected/path")
        assert response.status_code == 401
        assert "Bearer" in response.headers.get("WWW-Authenticate", "")

    def test_install_does_not_bypass_unrelated_well_known_paths(self, provider):
        """Only OAuth metadata paths bypass auth, not all of /.well-known/."""
        app = Starlette()
        provider.install(app)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/.well-known/change-password")
        assert response.status_code == 401, (
            "Non-OAuth /.well-known paths must stay behind bearer auth; "
            "only oauth-protected-resource, oauth-authorization-server, and "
            "jwks.json are exempt per RFC 9728 §2 / RFC 8414 §3."
        )

    def test_install_allows_oauth_metadata_subpaths(self, provider):
        """Delimited subpaths under OAuth metadata roots stay public (multi-zone)."""
        app = Starlette()
        provider.install(app)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/.well-known/oauth-protected-resource/some/zone-scoped/path"
        )
        assert response.status_code == 200


class TestAuthProviderLock:
    def test_init_lock_is_constructed_eagerly(self):
        """Avoid lazy lock construction; eager init removes any race question."""
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )
        import asyncio
        assert isinstance(provider._init_lock, asyncio.Lock)


class TestPackageHasNoMcpDependency:
    """The core KEP promise: keycardai-starlette does not import keycardai.mcp.*"""

    def test_starlette_source_does_not_import_mcp(self):
        import pkgutil

        import keycardai.starlette as pkg

        offenders = []
        for module_info in pkgutil.walk_packages(
            pkg.__path__, prefix=pkg.__name__ + "."
        ):
            module = __import__(module_info.name, fromlist=["__file__"])
            source_file = getattr(module, "__file__", None)
            if source_file:
                with open(source_file) as f:
                    source = f.read()
                if "from keycardai.mcp" in source or "import keycardai.mcp" in source:
                    offenders.append(module_info.name)
        assert not offenders, f"keycardai.starlette should not import keycardai.mcp: {offenders}"
