"""Tests for AuthProvider construction, install() wiring, and @protect()."""

import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.requests import Request
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

    def test_install_leaves_user_middleware_stack_empty(self, provider):
        """install() registers metadata routes only; protection is per-route."""
        app = FastAPI()
        provider.install(app)
        middleware_classes = [m.cls for m in app.user_middleware]
        assert BearerAuthMiddleware not in middleware_classes

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

    def test_routes_without_protect_decorator_stay_public(self, provider):
        """Routes without @auth.protect() are reachable without a bearer token."""
        app = FastAPI()
        provider.install(app)

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_install_allows_oauth_metadata_subpaths(self, provider):
        """Delimited subpaths under OAuth metadata roots resolve (multi-zone)."""
        app = Starlette()
        provider.install(app)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/.well-known/oauth-protected-resource/some/zone-scoped/path"
        )
        assert response.status_code == 200


class TestProtectDecorator:
    @pytest.fixture
    def provider(self):
        return AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )

    def test_no_args_returns_401_without_bearer(self, provider):
        app = FastAPI()
        provider.install(app)

        @app.get("/api/me")
        @provider.protect()
        async def me(request: Request):
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/me")
        assert response.status_code == 401
        assert "Bearer" in response.headers.get("WWW-Authenticate", "")

    def test_with_resource_returns_401_without_bearer(self, provider):
        from keycardai.oauth.server import AccessContext

        app = FastAPI()
        provider.install(app)

        @app.get("/api/calendar")
        @provider.protect("https://api.example.com")
        async def calendar(request: Request, access: AccessContext):
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/calendar")
        assert response.status_code == 401
        assert "Bearer" in response.headers.get("WWW-Authenticate", "")

    def test_no_args_does_not_require_access_context_param(self, provider):
        """Verify-only decorator works on a plain (request) signature."""
        from unittest.mock import AsyncMock, MagicMock

        # Stub the verifier so the decorator's verify call succeeds without
        # JWKS/network dependencies.
        token = MagicMock()
        token.token = "verified-token"
        provider.get_token_verifier = MagicMock(  # type: ignore[method-assign]
            return_value=MagicMock(
                enable_multi_zone=False,
                verify_token=AsyncMock(return_value=token),
            )
        )

        app = FastAPI()
        provider.install(app)

        @app.get("/api/me")
        @provider.protect()
        async def me(request: Request):
            return {"sub": request.state.keycardai_auth_info["access_token"]}

        client = TestClient(app)
        response = client.get(
            "/api/me", headers={"Authorization": "Bearer some-token"}
        )
        assert response.status_code == 200
        assert response.json() == {"sub": "verified-token"}

    def test_reuses_existing_state_set_by_middleware(self, provider):
        """If middleware already set request.state.keycardai_auth_info, the
        decorator reuses it instead of re-verifying. Use a verifier that would
        fail to prove it isn't called."""
        from unittest.mock import AsyncMock, MagicMock

        from starlette.middleware.base import BaseHTTPMiddleware

        provider.get_token_verifier = MagicMock(  # type: ignore[method-assign]
            return_value=MagicMock(
                enable_multi_zone=False,
                verify_token=AsyncMock(
                    side_effect=AssertionError(
                        "verify_token must not be called when state is preset"
                    )
                ),
            )
        )

        class PresetMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.keycardai_auth_info = {
                    "access_token": "preset",
                    "zone_id": None,
                    "resource_client_id": "preset",
                    "resource_server_url": "preset",
                }
                return await call_next(request)

        app = FastAPI()
        provider.install(app)
        app.add_middleware(PresetMiddleware)

        @app.get("/api/me")
        @provider.protect()
        async def me(request: Request):
            return {"sub": request.state.keycardai_auth_info["access_token"]}

        client = TestClient(app)
        response = client.get("/api/me")
        assert response.status_code == 200
        assert response.json() == {"sub": "preset"}


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
