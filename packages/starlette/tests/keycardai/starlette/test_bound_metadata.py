"""Conformance: oauth-metadata-endpoints v3.

Rows 8d-8f cover stateless APIs, which are not present here. Row 10a covers
detectable misregistration; Starlette cannot see an enclosing app's mount.
"""

import warnings
from functools import partial
from unittest.mock import MagicMock
from urllib.parse import unquote

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from keycardai.oauth.types.models import JsonWebKeySet
from keycardai.starlette import (
    AuthProvider,
    LegacyResourceBindingWarning,
    ProtectedResourceMetadata,
    ResourceBindingConfigError,
    auth_metadata_mount,
    requires,
    well_known_metadata_mount,
    well_known_metadata_routes,
)
from keycardai.starlette.handlers import (
    authorization_server_metadata,
    protected_resource_metadata,
)

ISSUER = "https://keycard.cloud"
ORIGIN = "https://api.example.com"
IDENTIFIER = f"{ORIGIN}/mcp"
RESOURCE_PATH = "/.well-known/oauth-protected-resource"
AS_PATH = "/.well-known/oauth-authorization-server"
ZONES = {"a": f"{ORIGIN}/a/mcp", "b": f"{ORIGIN}/b/mcp"}


def metadata_app(*, issuer=ISSUER, **options):
    return Starlette(routes=[auth_metadata_mount(issuer, **options)])


@pytest.mark.parametrize(
    ("suffix", "identifier"),
    [
        pytest.param("", ORIGIN, id="row_1"),
        pytest.param("/mcp", IDENTIFIER, id="row_7"),
        pytest.param("/mcp?v=1", f"{IDENTIFIER}?v=1", id="row_7_query"),
    ],
)
def test_row_1_7_publishes_exact_identifier_at_public_path(suffix, identifier):
    app = metadata_app(resource_identifier=identifier, jwks=JsonWebKeySet(keys=[]))
    with TestClient(app, base_url="http://internal") as client:
        response = client.get(
            f"{RESOURCE_PATH}{suffix}",
            headers={
                "X-Forwarded-Host": "attacker.example",
                "MCP-Protocol-Version": "2025-03-26",
            },
        )
        assert response.status_code == 200
        assert response.json()["resource"] == identifier
        assert response.json()["authorization_servers"] == [ISSUER]
        assert response.json()["jwks_uri"] == f"{ORIGIN}/.well-known/jwks.json"
        if "?" in suffix:
            assert client.get(f"{RESOURCE_PATH}/mcp").status_code == 404


@pytest.fixture
def upstream(monkeypatch):
    upstream = MagicMock()
    upstream.__enter__.return_value = upstream
    document = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/auth?resource=old&keep=1",
    }
    upstream.get.return_value = httpx.Response(
        200, json=document, request=httpx.Request("GET", ISSUER)
    )
    monkeypatch.setattr(
        "keycardai.starlette.handlers.metadata.httpx.Client", lambda **_: upstream
    )
    return upstream, document


def test_row_4a_4b_and_integration_2_zone_proxy(upstream):
    mock, document = upstream
    app = metadata_app(resource_identifiers_by_zone=ZONES)
    with TestClient(app) as client:
        for zone in ZONES:
            response = client.get(f"{AS_PATH}/{zone}")
            assert response.status_code == 200
            assert response.json() == document
            issuer = f"https://{zone}.keycard.cloud"
            mock.get.assert_called_with(f"{issuer}{AS_PATH}")
            prm = client.get(f"{RESOURCE_PATH}/{zone}/mcp")
            assert prm.json()["authorization_servers"] == [issuer]
        assert client.get(AS_PATH).status_code == 404
        assert client.get(f"{AS_PATH}/unknown").status_code == 404
    assert mock.get.call_count == 2


def test_row_4b_unknown_zone_can_match_another_route(upstream):
    async def fallback(request):
        return PlainTextResponse("other route")

    app = Starlette(
        routes=[
            auth_metadata_mount(ISSUER, resource_identifiers_by_zone=ZONES),
            Route(f"{AS_PATH}/unknown", fallback),
        ]
    )
    with TestClient(app) as client:
        assert client.get(f"{AS_PATH}/unknown").text == "other route"
    upstream[0].get.assert_not_called()


INVALID_ZONE_SEGMENTS = [
    "zone%23id",
    "zone%2Fid",
    "zone%40id",
    "zone%3Aid",
    "zone%2Eid",
    "a" * 64,
]
VALID_ZONE = "Zone-1"
ZONE_ISSUER = "https://zone-1.keycard.cloud"


@pytest.fixture
def zone_metadata_client(monkeypatch):
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(200, json={"issuer": ZONE_ISSUER})

    monkeypatch.setattr(
        httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(upstream))
    )
    with pytest.warns(LegacyResourceBindingWarning):
        legacy = auth_metadata_mount(ISSUER, enable_multi_zone=True)
    configured_issuers = {
        unquote(zone): ZONE_ISSUER for zone in [*INVALID_ZONE_SEGMENTS, VALID_ZONE]
    }
    configured = Route(
        f"/configured{AS_PATH}/{{zone_id:path}}",
        authorization_server_metadata(ISSUER, zone_issuers=configured_issuers),
    )
    app = Starlette(routes=[legacy, configured])

    async def strip_prefix(scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/gateway/edge/"):
            scope["path"] = scope["path"].removeprefix("/gateway/edge")
        await app(scope, receive, send)

    with TestClient(strip_prefix) as client:
        yield client, requests


@pytest.mark.parametrize("zone", INVALID_ZONE_SEGMENTS)
@pytest.mark.parametrize(
    "path",
    [
        f"{AS_PATH}/{{zone}}",
        f"{RESOURCE_PATH}/{{zone}}/mcp",
        f"/configured{AS_PATH}/{{zone}}",
    ],
)
def test_row_4b_8c_rejects_invalid_zone_ids(zone_metadata_client, path, zone):
    client, requests = zone_metadata_client
    response = client.get(path.format(zone=zone))
    assert response.status_code == 404
    assert requests == []


def test_row_4b_8c_valid_zone_id_publishes_and_proxies_metadata(zone_metadata_client):
    client, requests = zone_metadata_client
    response = client.get(f"{RESOURCE_PATH}/{VALID_ZONE}/mcp")
    assert response.status_code == 200
    assert response.json()["authorization_servers"] == [f"{ZONE_ISSUER}/"]
    assert requests == []

    for prefix in (AS_PATH, f"/configured{AS_PATH}"):
        response = client.get(f"{prefix}/{VALID_ZONE}")
        assert response.status_code == 200
        assert response.json() == {"issuer": ZONE_ISSUER}
    assert [str(request.url) for request in requests] == [f"{ZONE_ISSUER}{AS_PATH}"] * 2


@pytest.mark.parametrize("prefix", ["/gateway/edge", "/gateway%2Fedge"])
def test_row_8c_uses_zone_from_rewritten_path(zone_metadata_client, prefix):
    client, requests = zone_metadata_client
    resource = client.get(f"{prefix}{RESOURCE_PATH}/{VALID_ZONE}/mcp")
    assert resource.status_code == 200
    assert resource.json()["authorization_servers"] == [f"{ZONE_ISSUER}/"]

    authorization_server = client.get(f"{prefix}{AS_PATH}/{VALID_ZONE}")
    assert authorization_server.status_code == 200
    assert [str(request.url) for request in requests] == [f"{ZONE_ISSUER}{AS_PATH}"]


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"resource_identifier": "urn:example:service"}, id="row_8_urn"),
        pytest.param({"multi_resource": True, "audience": ["a", "b"]}, id="row_8_list"),
        pytest.param({"allow_any_audience": True}, id="rows_8_8a_any"),
        pytest.param({"multi_resource": True}, id="row_8a_no_audience"),
    ],
)
def test_row_8_8a_verifier_only_leaves_as_and_jwks_available(options, upstream):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app = metadata_app(jwks=JsonWebKeySet(keys=[]), **options)
    assert not caught
    with TestClient(app) as client:
        assert client.get(RESOURCE_PATH).status_code == 404
        assert client.get(AS_PATH).json() == upstream[1]
        assert client.get("/.well-known/jwks.json").json() == {"keys": []}


@pytest.mark.parametrize(
    "options",
    [
        {"multi_resource": True, "allow_any_audience": True},
        {"resource_identifier": IDENTIFIER, "resource_identifiers_by_zone": ZONES},
        {"resource_identifier": IDENTIFIER, "multi_resource": True},
        {"resource_identifier": IDENTIFIER, "allow_any_audience": True},
        {"resource_identifiers_by_zone": ZONES, "allow_any_audience": True},
    ],
)
def test_row_8b_conflicting_modes(options):
    with pytest.raises(ResourceBindingConfigError):
        metadata_app(**options)


def test_row_8c_warns_once_and_preserves_legacy_derivation():
    with pytest.warns(
        LegacyResourceBindingWarning, match="resource_identifier"
    ) as caught:
        app = metadata_app(multi_resource=False, allow_any_audience=False)
    assert len(caught) == 1
    with TestClient(app) as client:
        response = client.get(
            f"{RESOURCE_PATH}/mcp", headers={"X-Forwarded-Proto": "https"}
        )
        assert response.json()["resource"] == "https://testserver/mcp"


def test_row_8a_8c_application_managed_handler():
    metadata = ProtectedResourceMetadata(authorization_servers=[ISSUER])
    with pytest.warns(LegacyResourceBindingWarning, match="resource_identifier"):
        protected_resource_metadata(metadata)
    handler = protected_resource_metadata(metadata, multi_resource=True)
    bind = protected_resource_metadata(metadata, resource_identifier=IDENTIFIER)
    with TestClient(
        Starlette(
            routes=[
                Route(RESOURCE_PATH, handler),
                Route(f"{RESOURCE_PATH}/mcp", bind),
            ]
        )
    ) as client:
        assert client.get(RESOURCE_PATH).status_code == 404
        assert client.get(f"{RESOURCE_PATH}/mcp").json()["resource"] == IDENTIFIER


def test_row_1_explicit_handler_preserves_configured_issuers():
    metadata = ProtectedResourceMetadata(
        authorization_servers=[ISSUER, "https://another.example/issuer"],
    )
    path = f"{RESOURCE_PATH}/mcp"
    handler = protected_resource_metadata(metadata, resource_identifier=IDENTIFIER)
    with TestClient(Starlette(routes=[Route(path, handler)])) as client:
        assert client.get(path).json()["authorization_servers"] == [
            f"{ISSUER}/",
            "https://another.example/issuer",
        ]


@pytest.mark.parametrize(
    "identifier",
    ["https://例.example/mcp", "https://api.example.com/例"],
)
def test_row_1_rejects_non_ascii_resource_identifiers(identifier):
    with pytest.raises(ResourceBindingConfigError, match="ASCII"):
        metadata_app(resource_identifier=identifier)


def test_row_9_distinct_single_resource_routes():
    routes = []
    for path in ("/mcp", "/tools"):
        provider = AuthProvider(zone_url=ISSUER, resource_identifier=f"{ORIGIN}{path}")
        assert provider.get_token_verifier().audience == f"{ORIGIN}{path}"
        routes.extend(
            well_known_metadata_routes(
                ISSUER, resource_binding=provider.resource_binding
            )
        )
    with TestClient(Starlette(routes=routes)) as client:
        for path in ("/mcp", "/tools"):
            assert (
                client.get(f"{RESOURCE_PATH}{path}").json()["resource"]
                == f"{ORIGIN}{path}"
            )


def test_row_9a_zone_documents_and_port():
    options = {**ZONES, "c": "urn:example:resource"}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app = metadata_app(
            issuer=f"{ISSUER}:8443", resource_identifiers_by_zone=options
        )
    assert not caught
    with TestClient(app) as client:
        for zone, identifier in ZONES.items():
            document = client.get(f"{RESOURCE_PATH}/{zone}/mcp").json()
            assert document["resource"] == identifier
            assert document["authorization_servers"] == [
                f"https://{zone}.keycard.cloud:8443"
            ]
        assert client.get(f"{RESOURCE_PATH}/c/mcp").status_code == 404


def test_row_9b_zone_documents_require_issuer():
    with pytest.raises(ResourceBindingConfigError, match="base issuer"):
        metadata_app(issuer="", resource_identifiers_by_zone=ZONES)


def test_row_9c_shared_document_and_zone_proxy(upstream):
    app = metadata_app(
        multi_resource=True,
        resource_identifiers_by_zone=dict.fromkeys(ZONES, IDENTIFIER),
    )
    with TestClient(app) as client:
        document = client.get(f"{RESOURCE_PATH}/mcp").json()
        assert document["resource"] == IDENTIFIER
        assert document["authorization_servers"] == [
            "https://a.keycard.cloud",
            "https://b.keycard.cloud",
        ]
        assert client.get(f"{AS_PATH}/a").status_code == 200
        upstream[0].get.assert_called_once_with(f"https://a.keycard.cloud{AS_PATH}")
        assert client.get(AS_PATH).status_code == 404


def test_row_9d_shared_requires_identical_identifiers():
    with pytest.raises(ResourceBindingConfigError, match="identical"):
        metadata_app(multi_resource=True, resource_identifiers_by_zone=ZONES)


@pytest.mark.parametrize(
    "options",
    [
        {"resource_identifiers_by_zone": {}},
        {"resource_identifiers_by_zone": dict.fromkeys(ZONES, IDENTIFIER)},
        {"resource_identifier": "https:///relative"},
        {"resource_identifier": "https://user@api.example.com"},
        {"resource_identifier": "https://api.example.com/#"},
    ],
)
def test_row_1_9a_rejects_invalid_identifiers_and_ambiguous_routes(options):
    with pytest.raises(ResourceBindingConfigError):
        metadata_app(**options)


def test_row_10_root_registration_and_mounted_challenge():
    @requires("authenticated")
    async def protected(request):
        return PlainTextResponse("ok")

    identifier = f"{ORIGIN}/app/mcp"
    provider = AuthProvider(zone_url=ISSUER, resource_identifier=identifier)
    inner = Starlette(routes=[Route("/mcp", protected)])
    provider.install(inner)
    root = Starlette(
        routes=[
            *well_known_metadata_routes(
                ISSUER, resource_binding=provider.resource_binding
            ),
            Mount("/app", app=inner),
        ]
    )
    with TestClient(root) as client:
        public_path = f"{RESOURCE_PATH}/app/mcp"
        assert client.get(public_path).json()["resource"] == identifier
        challenge = client.get("/app/mcp").headers["WWW-Authenticate"]
        assert f'resource_metadata="{ORIGIN}{public_path}"' in challenge


def test_row_10a_explicit_wrong_mount_is_rejected():
    with pytest.raises(ResourceBindingConfigError, match="must be registered"):
        well_known_metadata_mount(
            ISSUER, "/app/.well-known", resource_identifier=IDENTIFIER
        )
