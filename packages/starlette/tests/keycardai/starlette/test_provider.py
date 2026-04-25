"""Tests for AuthProvider, KeycardAuthBackend, @requires and @auth.grant."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.testclient import TestClient

from keycardai.oauth.server import AccessContext
from keycardai.oauth.server.credentials import ClientSecret
from keycardai.oauth.server.exceptions import (
    AuthProviderConfigurationError,
    MissingAccessContextError,
)
from keycardai.starlette import (
    AuthProvider,
    KeycardAuthBackend,
    KeycardAuthCredentials,
    KeycardUser,
    grant,
    requires,
)
from keycardai.starlette.middleware.bearer import KeycardAuthError


def _stub_verifier(client_id: str = "test-client", scopes=None) -> MagicMock:
    """Build a mock TokenVerifier whose verify_token always returns a token."""
    if scopes is None:
        scopes = []
    token = MagicMock()
    token.token = "verified-token"
    token.client_id = client_id
    token.scopes = scopes
    return MagicMock(
        enable_multi_zone=False,
        verify_token=AsyncMock(return_value=token),
        verify_token_for_zone=AsyncMock(return_value=token),
    )


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

    def test_install_adds_authentication_middleware(self, provider):
        """install() registers AuthenticationMiddleware so request.user is populated."""
        app = FastAPI()
        provider.install(app)
        middleware_classes = [m.cls for m in app.user_middleware]
        assert AuthenticationMiddleware in middleware_classes

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

    def test_routes_without_requires_decorator_stay_public(self, provider):
        """Routes without @requires() are reachable without a bearer token."""
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


class TestKeycardAuthBackend:
    def test_no_auth_header_returns_none(self):
        """No Authorization header → backend returns None (anonymous)."""
        verifier = _stub_verifier()
        backend = KeycardAuthBackend(verifier)
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )
        provider.get_token_verifier = MagicMock(return_value=verifier)  # type: ignore[method-assign]

        app = FastAPI()
        provider.install(app)

        @app.get("/health")
        async def health():
            return {"ok": True}

        # Replace the middleware backend with our stub
        for m in app.user_middleware:
            if m.cls is AuthenticationMiddleware:
                m.kwargs["backend"] = backend

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/health")
        assert response.status_code == 200

    def test_malformed_authorization_header_returns_401_challenge(self):
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )
        verifier = _stub_verifier()
        provider.get_token_verifier = MagicMock(return_value=verifier)  # type: ignore[method-assign]

        app = FastAPI()
        provider.install(app)

        @app.get("/api/me")
        @requires("authenticated")
        async def me(request: Request):
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/me", headers={"Authorization": "MalformedHeader"})
        # Malformed format raises KeycardAuthError(status_code=400) which on_error
        # converts into a 4xx with a WWW-Authenticate challenge.
        assert response.status_code in (400, 401)
        assert "Bearer" in response.headers.get("WWW-Authenticate", "")

    def test_invalid_token_returns_401_challenge(self):
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )
        verifier = MagicMock(
            enable_multi_zone=False,
            verify_token=AsyncMock(return_value=None),
        )
        provider.get_token_verifier = MagicMock(return_value=verifier)  # type: ignore[method-assign]

        app = FastAPI()
        provider.install(app)

        @app.get("/api/me")
        @requires("authenticated")
        async def me(request: Request):
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/api/me", headers={"Authorization": "Bearer bad-token"}
        )
        assert response.status_code == 401
        challenge = response.headers.get("WWW-Authenticate", "")
        assert 'Bearer error="invalid_token"' in challenge
        assert "resource_metadata=" in challenge

    def test_keycard_auth_error_carries_oauth_metadata(self):
        exc = KeycardAuthError("invalid_token", "Token verification failed")
        assert exc.error == "invalid_token"
        assert exc.status_code == 401


class TestRequires:
    def test_anonymous_returns_401_with_www_authenticate(self):
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )

        app = FastAPI()
        provider.install(app)

        @app.get("/api/me")
        @requires("authenticated")
        async def me(request: Request):
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/me")
        assert response.status_code == 401
        challenge = response.headers.get("WWW-Authenticate", "")
        assert challenge.startswith("Bearer ")
        assert "resource_metadata=" in challenge

    def test_authenticated_with_scope_returns_200(self):
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )
        provider.get_token_verifier = MagicMock(  # type: ignore[method-assign]
            return_value=_stub_verifier(client_id="test-client", scopes=["read"])
        )

        app = FastAPI()
        provider.install(app)

        @app.get("/api/me")
        @requires("authenticated")
        async def me(request: Request):
            user: KeycardUser = request.user
            return {
                "client_id": user.client_id,
                "scopes": list(request.auth.scopes),
            }

        client = TestClient(app)
        response = client.get(
            "/api/me", headers={"Authorization": "Bearer some-token"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["client_id"] == "test-client"
        assert "authenticated" in body["scopes"]
        assert "read" in body["scopes"]

    def test_insufficient_scope_returns_403(self):
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )
        provider.get_token_verifier = MagicMock(  # type: ignore[method-assign]
            return_value=_stub_verifier(scopes=[])
        )

        app = FastAPI()
        provider.install(app)

        @app.get("/api/admin")
        @requires(["authenticated", "admin"])
        async def admin(request: Request):
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/api/admin", headers={"Authorization": "Bearer some-token"}
        )
        assert response.status_code == 403

    def test_module_requires_and_provider_requires_share_implementation(self):
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )
        # ``AuthProvider.requires`` is a staticmethod alias for the module-level
        # ``requires``; both decorate the same way. Class attribute access goes
        # through __get__, so compare the underlying function instead.
        assert AuthProvider.__dict__["requires"].__func__ is requires
        assert provider.requires is requires


class TestGrant:
    def test_missing_access_context_param_raises(self):
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )

        with pytest.raises(MissingAccessContextError):

            @provider.grant("https://api.example.com")
            async def handler(request: Request):
                return {"ok": True}

    def test_anonymous_returns_401_when_grant_used_without_requires(self):
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )

        app = FastAPI()
        provider.install(app)

        @app.get("/api/data")
        @provider.grant("https://api.example.com")
        async def get_data(request: Request, access: AccessContext):
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/data")
        assert response.status_code == 401
        assert "Bearer" in response.headers.get("WWW-Authenticate", "")

    def test_signature_hides_access_context_from_fastapi(self):
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )

        @provider.grant("https://api.example.com")
        async def handler(request: Request, access: AccessContext):
            return {"ok": True}

        import inspect as _inspect

        sig = _inspect.signature(handler)
        annotations = [p.annotation for p in sig.parameters.values()]
        assert AccessContext not in annotations

    def test_grant_populates_access_context(self):
        """End-to-end grant flow: stub the OAuth client + token exchange."""
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )
        provider.get_token_verifier = MagicMock(  # type: ignore[method-assign]
            return_value=_stub_verifier()
        )

        # Stub the per-request OAuth client and token exchange
        token_response = MagicMock()
        token_response.access_token = "downstream-token"
        token_response.token_type = "Bearer"
        token_response.expires_in = 3600

        async def fake_exchange(**kwargs):
            kwargs["access_context"].set_bulk_tokens(
                {"https://api.example.com": token_response}
            )
            return kwargs["access_context"]

        async def fake_get_or_create(_auth_info):
            return MagicMock()

        provider._get_or_create_client = fake_get_or_create  # type: ignore[method-assign]

        import keycardai.starlette.authorization as authz

        original_exchange = authz.exchange_tokens_for_resources
        authz.exchange_tokens_for_resources = fake_exchange  # type: ignore[assignment]

        try:
            app = FastAPI()
            provider.install(app)

            @app.get("/api/data")
            @requires("authenticated")
            @provider.grant("https://api.example.com")
            async def get_data(request: Request, access: AccessContext):
                return {
                    "token": access.access(
                        "https://api.example.com"
                    ).access_token,
                }

            client = TestClient(app)
            response = client.get(
                "/api/data", headers={"Authorization": "Bearer some-token"}
            )
            assert response.status_code == 200
            assert response.json() == {"token": "downstream-token"}
        finally:
            authz.exchange_tokens_for_resources = original_exchange  # type: ignore[assignment]


class TestAuthProviderLock:
    def test_init_lock_is_constructed_eagerly(self):
        """Avoid lazy lock construction; eager init removes any race question."""
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )
        import asyncio

        assert isinstance(provider._init_lock, asyncio.Lock)


class TestKeycardAuthCredentials:
    def test_authenticated_scope_always_present(self):
        creds = KeycardAuthCredentials(scopes=["read"])
        assert "authenticated" in creds.scopes
        assert "read" in creds.scopes

    def test_no_duplicate_authenticated_scope(self):
        creds = KeycardAuthCredentials(scopes=["authenticated", "read"])
        assert creds.scopes.count("authenticated") == 1


class TestKeycardUser:
    def test_user_exposes_keycard_fields(self):
        user = KeycardUser(
            access_token="tok",
            client_id="my-client",
            zone_id="zone-1",
            resource_server_url="https://api.example.com/.well-known/oauth-protected-resource/",
            scopes=["read"],
        )
        assert user.is_authenticated is True
        assert user.display_name == "my-client"
        assert user.access_token == "tok"
        assert user.zone_id == "zone-1"


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


class TestModuleLevelGrantExport:
    def test_grant_factory_accepts_provider(self):
        provider = AuthProvider(
            zone_id="test-zone",
            application_credential=ClientSecret(("cid", "csec")),
        )

        @grant(provider, "https://api.example.com")
        async def handler(request: Request, access: AccessContext):
            return {"ok": True}

        assert callable(handler)
