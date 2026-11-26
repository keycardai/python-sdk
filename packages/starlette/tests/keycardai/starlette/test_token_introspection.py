"""Bearer middleware v8 and Starlette/FastAPI integration v2 conformance."""

import json
import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.applications import Starlette
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from keycardai.oauth import AsyncClient, BasicAuth, ConfigError, NetworkError
from keycardai.oauth.http._wire import HttpResponse
from keycardai.oauth.server import ClientSecret, TokenVerifier
from keycardai.oauth.server._cache import JWKSKey
from keycardai.oauth.server.client_factory import DefaultClientFactory
from keycardai.starlette import AuthProvider, KeycardAuthBackend, keycard_on_error

ISSUER = "https://a.example.com"
OTHER_ISSUER = "https://b.example.com"
RESOURCE = "https://api.example.com/mcp"
CREDENTIAL = ClientSecret(("resource-server", "secret"))


@pytest.fixture(scope="module")
def signing_key():
    return RSAKey.generate_key(2048)


@pytest.fixture
def bearer(monkeypatch, signing_key):
    key = JWKSKey(
        key=signing_key.as_pem().decode(), timestamp=time.time(), algorithm="RS256"
    )
    monkeypatch.setattr(TokenVerifier, "_get_verification_key", AsyncMock(return_value=key))

    def sign(*, issuer=ISSUER, audience=RESOURCE, scope=None):
        token = jwt.encode(
            {"alg": "RS256", "kid": "signing-key", "typ": "at+jwt"},
            {
                "iss": issuer,
                "aud": audience,
                "sub": "user",
                "client_id": "other-client",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
                **({"scope": scope} if scope else {}),
            },
            signing_key,
        )
        return {"Authorization": f"Bearer {token}"}

    return sign


def reply(body, status=200):
    return HttpResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=body if isinstance(body, bytes) else json.dumps(body).encode(),
    )


class Wire(DefaultClientFactory):
    def __init__(self, *responses, missing_endpoint=False, endpoint=None):
        self.responses = iter(responses)
        self.missing_endpoint = missing_endpoint
        self.endpoint = endpoint
        self.requests = []

    def create_async_client(self, base_url, auth=None, config=None):
        class Transport:
            async def request_raw(_, request, *, timeout=None):
                self.requests.append((request, timeout))
                if request.method == "GET":
                    assert request.url == f"{base_url}/.well-known/oauth-authorization-server"
                    metadata = {"issuer": base_url}
                    if not self.missing_endpoint:
                        metadata["introspection_endpoint"] = (
                            self.endpoint or f"{base_url}/introspect"
                        )
                    return reply(metadata)
                response = next(self.responses)
                if isinstance(response, Exception):
                    raise response
                return response

        return AsyncClient(base_url, auth=auth, config=config, transport=Transport())


@pytest.fixture
def app():
    handled = []

    async def handler(request):
        handled.append(request.user.client_id)
        return PlainTextResponse("handler")

    return Starlette(routes=[Route("/mcp", handler)]), handled


def backend(app, wire, **options):
    server, _ = app
    verifier = TokenVerifier(
        issuer=options.pop("issuers", ISSUER),
        audience=RESOURCE,
        required_scopes=options.pop("required_scopes", None),
        client_factory=wire,
    )
    server.add_middleware(
        AuthenticationMiddleware,
        backend=KeycardAuthBackend(verifier, resource_identifier=RESOURCE, **options),
        on_error=keycard_on_error,
    )
    return TestClient(server)


def provider(wire, **options):
    return AuthProvider(
        zone_url=ISSUER, resource_identifier=RESOURCE, client_factory=wire, **options
    )


def assert_post(wire, bearer_header, credential_id, *, issuer=ISSUER, timeout=10.0):
    discovery, post = wire.requests
    assert discovery[1] == timeout
    assert "Authorization" not in discovery[0].headers
    request, request_timeout = post
    assert request_timeout == timeout
    assert (request.method, request.url) == ("POST", f"{issuer}/introspect")
    assert request.headers["Authorization"] == BasicAuth(
        credential_id, "secret"
    ).apply_headers()["Authorization"]
    assert parse_qs(request.body.decode()) == {
        "token": [bearer_header["Authorization"].removeprefix("Bearer ")]
    }


def assert_inactive(response):
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == (
        'Bearer error="invalid_token", error_description="Token is inactive", '
        'resource_metadata="https://api.example.com/.well-known/oauth-protected-resource/mcp"'
    )


def assert_unavailable(response):
    assert response.status_code == 503
    assert response.json() == {"error": "temporarily_unavailable"}
    assert "WWW-Authenticate" not in response.headers


@pytest.mark.parametrize("options", [{}, {"introspect": False}])
def test_row_1_starlette_opt_in_only(app, bearer, options):
    wire = Wire()
    provider(wire, **options).install(app[0])
    response = TestClient(app[0]).get("/mcp", headers=bearer())
    assert response.status_code == 200
    assert app[1] == ["other-client"]
    assert wire.requests == []


@pytest.mark.parametrize("mount", ["install", "get_routes"])
def test_row_2_starlette_passes_credential(app, bearer, mount):
    wire = Wire(reply({"active": True, "client_id": "introspection-claim"}))
    auth = provider(wire, introspect=True, application_credential=CREDENTIAL)
    if mount == "install":
        auth.install(app[0])
        server = app[0]
    else:
        server = Starlette(routes=auth.get_routes(app[0]))
    headers = bearer()
    response = TestClient(server).get("/mcp", headers=headers)
    assert response.status_code == 200
    assert app[1] == ["other-client"]
    assert_post(wire, headers, "resource-server")


def test_row_3_starlette_inactive(app, bearer):
    wire = Wire(reply({"active": False}))
    provider(wire, introspect=True, application_credential=CREDENTIAL).install(app[0])
    assert_inactive(TestClient(app[0]).get("/mcp", headers=bearer()))
    assert app[1] == []


def test_row_4_starlette_introspection_failure(app, bearer):
    wire = Wire(reply({"error": "invalid_client"}, 401))
    provider(wire, introspect=True, application_credential=CREDENTIAL).install(app[0])
    assert_unavailable(TestClient(app[0]).get("/mcp", headers=bearer()))
    assert app[1] == []


def test_row_5_starlette_credential_requires_client_authentication():
    with pytest.raises(ConfigError, match="does not provide client authentication"):
        provider(Wire(), introspect=True, application_credential=object())


@pytest.mark.parametrize(
    ("issuer", "credential", "credential_id"),
    [
        (ISSUER, CREDENTIAL, "resource-server"),
        (
            OTHER_ISSUER,
            ClientSecret(
                {ISSUER: ("unused", "secret"), OTHER_ISSUER: ("zone-b-owner", "secret")}
            ),
            "zone-b-owner",
        ),
    ],
)
def test_row_18_verified_issuer_and_resource_server_credential(
    app, bearer, issuer, credential, credential_id
):
    wire = Wire(reply({"active": True}))
    client = backend(
        app, wire, issuers=[ISSUER] if issuer == ISSUER else [ISSUER, OTHER_ISSUER],
        introspect=True, credential=credential, introspection_timeout=2.0,
    )
    headers = bearer(issuer=issuer)
    assert client.get("/mcp", headers=headers).text == "handler"
    assert app[1] == ["other-client"]
    assert_post(wire, headers, credential_id, issuer=issuer, timeout=2.0)


def test_row_19_inactive_even_if_a_later_audience_matches(app, bearer):
    wire = Wire(reply({"active": False}))
    client = backend(app, wire, introspect=True, credential=CREDENTIAL)
    headers = bearer(audience=["https://other.example.com/mcp", RESOURCE])
    assert_inactive(client.get("/mcp", headers=headers))
    assert_post(wire, headers, "resource-server")
    assert app[1] == []


@pytest.mark.parametrize(
    ("failure", "missing_endpoint"),
    [
        (NetworkError(TimeoutError()), False),
        (reply(b"unavailable", 502), False),
        (reply({"error": "invalid_client"}, 401), False),
        (reply({"error": "server_error"}, 500), False),
        (reply(b"{"), False),
        (reply({"active": "true"}), False),
        (None, True),
    ],
    ids=["transport", "http", "invalid-client", "server-error", "json", "active", "metadata"],
)
def test_row_20_fail_closed_without_challenge(app, bearer, failure, missing_endpoint):
    wire = Wire(failure, missing_endpoint=missing_endpoint)
    client = backend(app, wire, introspect=True, credential=CREDENTIAL)
    assert_unavailable(client.get("/mcp", headers=bearer()))
    assert app[1] == []


@pytest.mark.parametrize(
    ("options", "issuer", "status"),
    [
        ({}, ISSUER, 200),
        ({"introspect": False}, ISSUER, 200),
        ({"introspect": True}, "https://untrusted.example.com", 401),
    ],
)
def test_row_21_no_introspection_when_disabled_or_locally_invalid(
    app, bearer, options, issuer, status
):
    wire = Wire()
    client = backend(app, wire, credential=CREDENTIAL, **options)
    assert client.get("/mcp", headers=bearer(issuer=issuer)).status_code == status
    assert wire.requests == []
    assert app[1] == (["other-client"] if status == 200 else [])


def test_row_22_same_token_introspected_on_each_request(app, bearer):
    wire = Wire(reply({"active": True}), reply({"active": False}))
    client = backend(app, wire, introspect=True, credential=CREDENTIAL)
    headers = bearer()
    with client:
        assert client.get("/mcp", headers=headers).text == "handler"
        assert_inactive(client.get("/mcp", headers=headers))
    assert [request.method for request, _ in wire.requests] == ["GET", "POST", "POST"]
    assert wire.requests[1][0].body == wire.requests[2][0].body
    assert app[1] == ["other-client"]


@pytest.mark.parametrize(
    ("issuers", "credential", "match"),
    [
        (ISSUER, None, "requires a credential"),
        (ISSUER, object(), "does not provide client authentication"),
        ([ISSUER, OTHER_ISSUER], CREDENTIAL, "requires zone-keyed credentials"),
    ],
)
def test_row_23_invalid_credentials_fail_at_construction(issuers, credential, match):
    verifier = TokenVerifier(issuer=issuers, audience=RESOURCE)
    with pytest.raises(ConfigError, match=match):
        KeycardAuthBackend(verifier, introspect=True, credential=credential)


def test_row_24_no_introspection_request_for_zone_without_credential(app, bearer):
    wire = Wire()
    client = backend(
        app, wire, issuers=[ISSUER, OTHER_ISSUER], introspect=True,
        credential=ClientSecret({ISSUER: ("resource-server", "secret")}),
    )
    assert_unavailable(client.get("/mcp", headers=bearer(issuer=OTHER_ISSUER)))
    assert wire.requests == []
    assert app[1] == []


def test_introspection_never_discovers_metadata_over_plaintext(app, bearer):
    issuer = "http://a.example.com"
    wire = Wire(reply({"active": True}), endpoint="https://other.example.com/introspect")
    client = backend(app, wire, issuers=issuer, introspect=True, credential=CREDENTIAL)
    assert_unavailable(client.get("/mcp", headers=bearer(issuer=issuer)))
    assert wire.requests == []
    assert app[1] == []


def test_row_25_insufficient_scope_is_checked_before_introspection(app, bearer):
    wire = Wire()
    client = backend(
        app, wire, introspect=True, credential=CREDENTIAL, required_scopes=["write"]
    )
    response = client.get("/mcp", headers=bearer(scope="read"))
    assert response.status_code == 403
    assert 'error="insufficient_scope"' in response.headers["WWW-Authenticate"]
    assert wire.requests == []
    assert app[1] == []


def test_logs_distinct_failures_once(app, bearer, caplog):
    wire = Wire(
        reply({"error": "invalid_client"}, 401),
        reply({"error": "invalid_client"}, 401),
        reply({"error": "server_error"}, 500),
    )
    headers = bearer()
    client = backend(app, wire, introspect=True, credential=CREDENTIAL)
    for _ in range(3):
        assert_unavailable(client.get("/mcp", headers=headers))
    logs = [record.message for record in caplog.records if "Token introspection failed" in record.message]
    assert len(logs) == 2
    assert "invalid_client" in logs[0]
    assert "server_error" in logs[1]
