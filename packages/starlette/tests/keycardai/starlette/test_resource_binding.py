"""Conformance: bearer-token-verification-middleware v7 and route-level-auth-gating v2.

Bearer row 7a applies only to the eventual release removing legacy support.
"""

import time
import warnings
from unittest.mock import AsyncMock, Mock

import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from keycardai.oauth.server import AccessContext
from keycardai.oauth.server._cache import JWKSKey
from keycardai.oauth.server.exceptions import AuthProviderConfigurationError
from keycardai.oauth.server.verifier import TokenVerifier
from keycardai.starlette import (
    AuthProvider,
    KeycardAuthBackend,
    LegacyResourceBindingWarning,
    ResourceBindingConfigError,
    protected_router,
    requires,
)

ISSUER = "https://keycard.cloud"
ORIGIN = "https://api.example.com"
IDENTIFIER = f"{ORIGIN}/mcp"
METADATA = f"{ORIGIN}/.well-known/oauth-protected-resource"
ZONES = {"a": f"{ORIGIN}/a/mcp", "b": f"{ORIGIN}/b/mcp"}
AUDIENCES = ["resource-a", "resource-b"]


@pytest.fixture(scope="module")
def signing_key():
    return RSAKey.generate_key(2048)


@pytest.fixture
def token_headers(monkeypatch, signing_key):
    verification_key = JWKSKey(
        key=signing_key.as_pem().decode(), timestamp=time.time(), algorithm="RS256"
    )
    monkeypatch.setattr(
        TokenVerifier, "_get_verification_key", AsyncMock(return_value=verification_key)
    )

    def create(aud, issuer=ISSUER, *, invalid_signature=False):
        token = jwt.encode(
            {"alg": "RS256", "kid": "binding-key"},
            {
                "iss": issuer,
                "aud": aud,
                "sub": "caller",
                "client_id": "client",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
            },
            RSAKey.generate_key(2048) if invalid_signature else signing_key,
        )
        return {"Authorization": f"Bearer {token}"}

    return create


async def handled(request):
    return PlainTextResponse("ok")


def protected_app(provider):
    inner = Starlette(routes=[Route("/{rest:path}", handled), Route("/", handled)])
    return Starlette(
        routes=protected_router(
            issuer=provider.issuer,
            app=inner,
            verifier=provider.get_token_verifier(),
            enable_multi_zone=provider.enable_multi_zone,
            require_authentication=True,
            resource_binding=provider.resource_binding,
        )
    )


def request(client, path, headers=None, *, status, metadata=None):
    response = client.get(path, headers=headers)
    assert response.status_code == status
    challenge = response.headers.get("WWW-Authenticate", "")
    if status in {401, 403}:
        assert challenge.startswith("Bearer")
        if metadata is None:
            assert "resource_metadata" not in challenge
        else:
            assert f'resource_metadata="{metadata}"' in challenge
    return challenge


@pytest.mark.parametrize(
    ("path", "headers", "expected"),
    [
        pytest.param("/mcp", None, f"{METADATA}/mcp", id="row_12_proxy"),
        pytest.param("/tools", None, None, id="row_12a_path"),
        pytest.param("/mcp?other=1", None, None, id="row_12a_query"),
        pytest.param(
            "/mcp",
            {
                "Host": "attacker.example",
                "X-Forwarded-Host": "elsewhere.example",
                "X-Forwarded-Proto": "https",
            },
            f"{METADATA}/mcp",
            id="row_12c_untrusted_headers",
        ),
    ],
)
def test_bearer_challenge_matches_only_path_and_query(path, headers, expected):
    provider = AuthProvider(zone_url=ISSUER, resource_identifier=IDENTIFIER)
    with TestClient(protected_app(provider)) as client:
        request(client, path, headers, status=401, metadata=expected)


def test_bearer_row_1_query_matches_verbatim():
    provider = AuthProvider(zone_url=ISSUER, resource_identifier=f"{IDENTIFIER}?v=1")
    with TestClient(protected_app(provider)) as client:
        request(client, "/mcp?v=1", status=401, metadata=f"{METADATA}/mcp?v=1")
        request(client, "/mcp", status=401)


def test_bearer_row_12b_bare_origin_audience_is_exact(token_headers):
    provider = AuthProvider(zone_url=ISSUER, resource_identifier=ORIGIN)
    with TestClient(protected_app(provider)) as client:
        request(client, "/", status=401, metadata=METADATA)
        challenge = request(
            client, "/", token_headers(f"{ORIGIN}/"), status=401, metadata=METADATA
        )
        assert 'error="invalid_token"' in challenge
        request(client, "/", token_headers(ORIGIN), status=200)


def test_bearer_row_12d_mounted_target_keeps_prefix():
    provider = AuthProvider(zone_url=ISSUER, resource_identifier=f"{ORIGIN}/app/mcp")
    app = Starlette(routes=[Mount("/app", app=protected_app(provider))])
    with TestClient(app) as client:
        request(client, "/app/mcp", status=401, metadata=f"{METADATA}/app/mcp")


def test_bearer_rows_8_15_and_integration_2_same_issuer_different_audience(
    token_headers,
):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app = protected_app(
            AuthProvider(zone_url=ISSUER, resource_identifier=IDENTIFIER)
        )
    assert not caught
    with TestClient(app) as client:
        request(client, "/mcp", token_headers(IDENTIFIER), status=200)
        request(client, "/mcp", token_headers(["other", IDENTIFIER]), status=200)
        challenge = request(
            client,
            "/mcp",
            token_headers(f"{ORIGIN}/other"),
            status=401,
            metadata=f"{METADATA}/mcp",
        )
    assert 'error="invalid_token"' in challenge


def test_bearer_rows_8a_8b_8c_8d_8f_list_intersection_without_discovery(token_headers):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app = protected_app(
            AuthProvider(zone_url=ISSUER, multi_resource=True, audience=AUDIENCES)
        )
    assert not caught
    with TestClient(app) as client:
        request(client, "/mcp", token_headers("resource-b"), status=200)
        request(client, "/mcp", token_headers(["resource-c", "resource-b"]), status=200)
        for aud in ("resource-c", ["resource-c", "resource-d"]):
            challenge = request(client, "/mcp", token_headers(aud), status=401)
            assert 'error="invalid_token"' in challenge


@pytest.mark.parametrize(
    ("options", "aud", "invalid_signature"),
    [
        pytest.param(
            {"allow_any_audience": True}, "another-resource", True, id="row_8g"
        ),
        pytest.param(
            {"resource_identifier": "urn:example:service"},
            "urn:example:service",
            False,
            id="row_8h",
        ),
    ],
)
def test_bearer_verifier_only_modes(token_headers, options, aud, invalid_signature):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app = protected_app(AuthProvider(zone_url=ISSUER, **options))
    assert not caught
    with TestClient(app) as client:
        request(client, "/mcp", token_headers(aud), status=200)
        failed = token_headers(
            aud if invalid_signature else "urn:example:other",
            invalid_signature=invalid_signature,
        )
        assert 'error="invalid_token"' in request(client, "/mcp", failed, status=401)


def test_bearer_rows_16_16a_16c_zone_is_selected_before_verification(token_headers):
    provider = AuthProvider(
        zone_url=ISSUER, enable_multi_zone=True, resource_identifiers_by_zone=ZONES
    )
    with TestClient(protected_app(provider)) as client:
        request(client, "/a/mcp", status=401, metadata=f"{METADATA}/a/mcp")
        valid = token_headers(ZONES["a"], "https://a.keycard.cloud")
        request(client, "/a/mcp", valid, status=200)
        wrong = token_headers(ZONES["b"], "https://a.keycard.cloud")
        challenge = request(
            client, "/a/mcp", wrong, status=401, metadata=f"{METADATA}/a/mcp"
        )
        assert 'error="invalid_token"' in challenge
        request(client, "/", status=401)
        request(client, "/unknown/mcp", status=401)


def test_bearer_row_16_provider_install_resolves_routed_zone(token_headers):
    app = Starlette(
        routes=[Route("/{zone_id}/mcp", requires("authenticated")(handled))]
    )
    AuthProvider(
        zone_url=ISSUER, enable_multi_zone=True, resource_identifiers_by_zone=ZONES
    ).install(app)
    with TestClient(app) as client:
        request(client, "/a/mcp", status=401, metadata=f"{METADATA}/a/mcp")
        request(
            client,
            "/a/mcp",
            token_headers(ZONES["a"], "https://a.keycard.cloud"),
            status=200,
        )
        request(client, "/unknown/mcp", status=401)


def test_review_4703017729_per_zone_install_keeps_health_public(token_headers):
    app = Starlette(routes=[Route("/health", handled)])
    AuthProvider(
        enable_multi_zone=True, resource_identifiers_by_zone=ZONES
    ).install(app)
    with TestClient(app) as client:
        request(client, "/health", status=200)
        token = token_headers(ZONES["a"], "https://a.keycard.cloud")
        request(client, "/health", token, status=401)


def test_review_finding_1_protected_router_verifies_application_routes(token_headers):
    async def authenticated(request):
        return PlainTextResponse(str(request.user.is_authenticated))

    path = "/.well-known/oauth-protected-resource/application"
    inner = Starlette(routes=[Route(path, authenticated)])
    provider = AuthProvider(zone_url=ISSUER, resource_identifier=IDENTIFIER)
    app = Starlette(
        routes=protected_router(
            issuer=provider.issuer,
            app=inner,
            verifier=provider.get_token_verifier(),
            require_authentication=True,
            resource_binding=provider.resource_binding,
        )
    )
    with TestClient(app) as client:
        assert client.get(path, headers=token_headers(IDENTIFIER)).text == "True"
        assert (
            client.get("/.well-known/oauth-protected-resource/mcp").status_code == 200
        )


def test_review_finding_1_installed_zone_metadata_remains_public():
    provider = AuthProvider(
        zone_url=ISSUER,
        enable_multi_zone=True,
        resource_identifiers_by_zone=ZONES,
    )
    app = Starlette()
    provider.install(app)
    with TestClient(app) as client:
        response = client.get("/.well-known/oauth-protected-resource/a/mcp")
        assert response.status_code == 200
        assert response.json()["resource"] == ZONES["a"]


def test_review_finding_2_grant_uses_verified_issuer_client(monkeypatch, token_headers):
    issuer = "https://login.example.com:8443"
    zone_a = "https://a.login.example.com:8443"
    zone_b = "https://b.login.example.com:8443"
    factory = Mock()
    clients = {zone_a: Mock(), zone_b: Mock()}
    factory.create_async_client.side_effect = lambda *, base_url, **_: clients[base_url]
    exchange = AsyncMock()
    monkeypatch.setattr(
        "keycardai.starlette.authorization.exchange_tokens_for_resources", exchange
    )
    provider = AuthProvider(
        zone_url=issuer,
        resource_identifiers_by_zone=dict.fromkeys(ZONES, IDENTIFIER),
        multi_resource=True,
        audience=[IDENTIFIER],
        client_factory=factory,
    )

    @provider.grant("https://downstream.example")
    async def endpoint(request, access: AccessContext):
        assert not access.has_errors()
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/mcp", endpoint)])
    provider.install(app)
    with TestClient(app) as client:
        for issuer_url in (zone_a, zone_b, zone_a):
            request(client, "/mcp", token_headers(IDENTIFIER, issuer_url), status=200)

    assert [
        args.kwargs["base_url"] for args in factory.create_async_client.call_args_list
    ] == [
        zone_a,
        zone_b,
    ]
    assert [args.kwargs["client"] for args in exchange.await_args_list] == [
        clients[zone_a],
        clients[zone_b],
        clients[zone_a],
    ]
    assert [
        args.kwargs["auth_info"]["zone_id"] for args in exchange.await_args_list
    ] == [
        "a",
        "b",
        "a",
    ]


@pytest.mark.parametrize("zone_id", [None, "unknown"])
@pytest.mark.asyncio
async def test_review_finding_2_grant_rejects_missing_or_unknown_zone(zone_id):
    factory = Mock()
    provider = AuthProvider(
        zone_url=ISSUER,
        resource_identifiers_by_zone=dict.fromkeys(ZONES, IDENTIFIER),
        multi_resource=True,
        audience=[IDENTIFIER],
        client_factory=factory,
    )
    with pytest.raises(AuthProviderConfigurationError, match="trusted zone"):
        await provider._get_or_create_client({"zone_id": zone_id})
    factory.create_async_client.assert_not_called()


def test_bearer_prebuilt_verifier_mismatch_is_detectable():
    with pytest.raises(ResourceBindingConfigError, match="audience"):
        KeycardAuthBackend(
            TokenVerifier(issuer=ISSUER, audience="wrong"),
            resource_identifier=IDENTIFIER,
        )
    with pytest.raises(ResourceBindingConfigError, match="trusted issuers"):
        KeycardAuthBackend(
            TokenVerifier(issuer=["https://a.keycard.cloud"], audience=[IDENTIFIER]),
            multi_resource=True,
            resource_identifiers_by_zone=dict.fromkeys(ZONES, IDENTIFIER),
            audience=[IDENTIFIER],
        )


def test_bearer_row_17_shared_binding_accepts_exactly_the_trusted_issuers(
    token_headers,
):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        provider = AuthProvider(
            zone_url=ISSUER,
            resource_identifiers_by_zone=dict.fromkeys(ZONES, IDENTIFIER),
            multi_resource=True,
            audience=[IDENTIFIER],
        )
        app = protected_app(provider)
    assert not caught
    with TestClient(app) as client:
        request(client, "/mcp", status=401, metadata=f"{METADATA}/mcp")
        for zone in ZONES:
            request(
                client,
                "/mcp",
                token_headers(IDENTIFIER, f"https://{zone}.keycard.cloud"),
                status=200,
            )
        request(
            client,
            "/mcp",
            token_headers(IDENTIFIER),
            status=401,
            metadata=f"{METADATA}/mcp",
        )


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"multi_resource": True, "audience": []}, id="row_8e"),
        pytest.param({"multi_resource": True}, id="row_14b_missing"),
        pytest.param({"multi_resource": True, "audience": "a"}, id="row_14b_scalar"),
        pytest.param({"multi_resource": True, "audience": ZONES}, id="row_14b_map"),
        pytest.param(
            {"resource_identifier": IDENTIFIER, "allow_any_audience": True}, id="row_13"
        ),
        pytest.param(
            {"resource_identifier": IDENTIFIER, "resource_identifiers_by_zone": ZONES},
            id="row_16b_single",
        ),
        pytest.param(
            {"allow_any_audience": True, "resource_identifiers_by_zone": ZONES},
            id="row_16b_any",
        ),
        pytest.param({"resource_identifiers_by_zone": ZONES}, id="row_16d"),
        pytest.param(
            {
                "multi_resource": True,
                "resource_identifiers_by_zone": ZONES,
                "audience": [ZONES["a"]],
            },
            id="row_17a",
        ),
    ],
)
def test_bearer_invalid_configuration_never_warns(options):
    with (
        warnings.catch_warnings(record=True) as caught,
        pytest.raises(ResourceBindingConfigError),
    ):
        warnings.simplefilter("always")
        AuthProvider(zone_url=ISSUER, **options)
    assert not caught


@pytest.mark.parametrize(
    ("audience", "path", "aud", "token_issuer", "warning"),
    [
        pytest.param(
            None, "/mcp", "ignored", ISSUER, "resource_identifier", id="row_7"
        ),
        pytest.param(
            AUDIENCES, "/mcp", "resource-b", ISSUER, "resource_identifier", id="row_14"
        ),
        pytest.param(
            "resource-b",
            "/mcp",
            "resource-b",
            ISSUER,
            "resource_identifier",
            id="row_14a_scalar",
        ),
        pytest.param(
            ZONES,
            "/a/mcp",
            ZONES["a"],
            "https://a.keycard.cloud",
            "resource_identifiers_by_zone",
            id="row_14a_map",
        ),
    ],
)
def test_bearer_legacy_modes_preserve_policy_and_warn_once(
    token_headers, audience, path, aud, token_issuer, warning
):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        provider = AuthProvider(
            zone_url=ISSUER,
            audience=audience,
            enable_multi_zone=isinstance(audience, dict),
        )
        app = protected_app(provider)
        with TestClient(app) as client:
            request(client, path, token_headers(aud, token_issuer), status=200)
            challenge = request(
                client,
                path,
                {"Host": "legacy.example", "X-Forwarded-Proto": "https"},
                status=401,
                metadata=f"https://legacy.example/.well-known/oauth-protected-resource{path}",
            )
            assert challenge
    assert len(caught) == 1
    assert caught[0].category is LegacyResourceBindingWarning
    assert warning in str(caught[0].message)
    assert "resource_identifier" in str(caught[0].message)


@pytest.mark.parametrize(
    ("options", "path", "metadata"),
    [
        pytest.param(
            {"resource_identifier": f"{IDENTIFIER}?v=1"},
            "/mcp?v=1",
            f"{METADATA}/mcp?v=1",
            id="row_2",
        ),
        pytest.param({"resource_identifier": ORIGIN}, "/", METADATA, id="row_2a"),
        pytest.param(
            {"resource_identifier": "urn:example:service"}, "/", None, id="row_7_urn"
        ),
        pytest.param({"allow_any_audience": True}, "/", None, id="row_7_any"),
    ],
)
def test_gating_anonymous_challenge_reuses_binding(options, path, metadata):
    app = Starlette(routes=[Route("/{rest:path}", requires("authenticated")(handled))])
    AuthProvider(zone_url=ISSUER, **options).install(app)
    with TestClient(app) as client:
        request(client, path, status=401, metadata=metadata)


def test_gating_row_6_scope_challenge_omits_discovery_at_another_path(token_headers):
    app = Starlette(routes=[Route("/tools", requires("admin")(handled))])
    AuthProvider(zone_url=ISSUER, resource_identifier=IDENTIFIER).install(app)
    with TestClient(app) as client:
        challenge = request(client, "/tools", token_headers(IDENTIFIER), status=403)
    assert 'error="insufficient_scope"' in challenge


def test_bearer_scope_failure_preserves_the_matching_binding(token_headers):
    provider = AuthProvider(
        zone_url=ISSUER, resource_identifier=IDENTIFIER, required_scopes=["admin"]
    )
    with TestClient(protected_app(provider)) as client:
        challenge = request(
            client,
            "/mcp",
            token_headers(IDENTIFIER),
            status=403,
            metadata=f"{METADATA}/mcp",
        )
    assert 'error="insufficient_scope"' in challenge
