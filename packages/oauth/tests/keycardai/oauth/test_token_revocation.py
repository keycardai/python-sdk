"""Token revocation (RFC 7009) on AsyncClient.

Test names start with the row of the unit test table in the Keycard SDK spec
(specs/oauth-client/token-revocation.md). The tests run against a fake
transport.
"""

import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest

from keycardai.oauth import (
    AsyncClient,
    AuthorizationServerMetadata,
    BasicAuth,
    ClientAuthentication,
    ClientConfig,
    ConfigError,
    Endpoints,
    NetworkError,
    OAuthHttpError,
    TokenRevocationError,
)
from keycardai.oauth.client import resolve_endpoints
from keycardai.oauth.exceptions import PERMANENT_ERROR_CODES
from keycardai.oauth.http._wire import HttpRequest, HttpResponse
from keycardai.oauth.server.credentials import (
    ClientSecret,
    WebIdentity,
    WorkloadIdentity,
)
from keycardai.oauth.types.oauth import TokenTypeHint

ISSUER = "https://zone.example.com"
DISCOVERY_URL = f"{ISSUER}/.well-known/oauth-authorization-server"
REVOCATION_ENDPOINT = "https://zone.example.com/oauth/2/revoke"
TOKEN_ENDPOINT = "https://zone.example.com/oauth/2/token"
JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

pytestmark = pytest.mark.asyncio

METADATA = AuthorizationServerMetadata(
    issuer=ISSUER,
    token_endpoint=TOKEN_ENDPOINT,
    revocation_endpoint=REVOCATION_ENDPOINT,
)
NO_REVOCATION_METADATA = AuthorizationServerMetadata(
    issuer=ISSUER, token_endpoint=TOKEN_ENDPOINT
)


class FakeTransport:
    """Answers each request with the next queued response or exception."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[HttpRequest] = []
        self.timeouts: list[float | None] = []

    async def request_raw(
        self, req: HttpRequest, *, timeout: float | None = None
    ) -> HttpResponse:
        self.requests.append(req)
        self.timeouts.append(timeout)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeCredential:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def client_authentication(
        self, *, issuer: str, endpoint: str
    ) -> ClientAuthentication:
        self.calls.append((issuer, endpoint))
        return ClientAuthentication(
            headers={"X-Client-Auth": "credential-header"},
            form_fields={
                "client_assertion": "assertion",
                "client_assertion_type": JWT_BEARER,
            },
        )


class ExchangeOnlyCredential:
    """A credential with token exchange and no client authentication."""

    async def prepare_token_exchange_request(
        self, client, subject_token, resource, auth_info=None
    ):
        raise AssertionError("not used")


def respond(status: int, body: dict | bytes = b"") -> HttpResponse:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return HttpResponse(status=status, headers={}, body=raw)


def make_client(transport: FakeTransport, **kwargs) -> AsyncClient:
    return AsyncClient(
        ISSUER,
        transport=transport,
        config=ClientConfig(
            enable_metadata_discovery=False, auto_register_client=False, timeout=7.0
        ),
        **kwargs,
    )


def discovering_client(transport: FakeTransport) -> AsyncClient:
    return AsyncClient(
        ISSUER,
        transport=transport,
        config=ClientConfig(auto_register_client=False, timeout=7.0),
    )


def form(req: HttpRequest) -> dict[str, list[str]]:
    assert req.body is not None
    return parse_qs(req.body.decode())


def jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def basic(client_id: str, client_secret: str) -> str:
    return BasicAuth(client_id, client_secret).apply_headers()["Authorization"]


async def test_row_1_public_client_revokes_refresh_token_at_discovered_endpoint():
    discovery_body = {
        "issuer": ISSUER,
        "token_endpoint": TOKEN_ENDPOINT,
        "revocation_endpoint": REVOCATION_ENDPOINT,
    }
    transport = FakeTransport(respond(200, discovery_body), respond(200))
    client = discovering_client(transport)

    await client.revoke_token("rt", client_id="public-app")

    discovery, revoke = transport.requests
    assert discovery.url == DISCOVERY_URL
    assert revoke.method == "POST"
    assert revoke.url == REVOCATION_ENDPOINT
    assert revoke.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert "Authorization" not in revoke.headers
    assert form(revoke) == {
        "token": ["rt"],
        "token_type_hint": ["refresh_token"],
        "client_id": ["public-app"],
    }


async def test_row_2_client_secret_uses_basic_without_body_client_id():
    transport = FakeTransport(respond(200))

    await make_client(transport).revoke_token(
        "rt", client_id="app", client_secret="s3cret", metadata=METADATA
    )

    (revoke,) = transport.requests
    assert revoke.headers["Authorization"] == basic("app", "s3cret")
    assert form(revoke) == {"token": ["rt"], "token_type_hint": ["refresh_token"]}


async def test_row_2_client_secret_credential_uses_basic_without_body_client_id():
    transport = FakeTransport(respond(200))

    await make_client(transport).revoke_token(
        "rt", credential=ClientSecret(("app", "s3cret")), metadata=METADATA
    )

    (revoke,) = transport.requests
    assert revoke.headers["Authorization"] == basic("app", "s3cret")
    assert form(revoke) == {"token": ["rt"], "token_type_hint": ["refresh_token"]}


async def test_row_2_client_auth_strategy_authenticates_when_no_inputs():
    transport = FakeTransport(respond(200))
    client = make_client(transport, auth=BasicAuth("app", "s3cret"))

    await client.revoke_token("rt", metadata=METADATA)

    (revoke,) = transport.requests
    assert revoke.headers["Authorization"] == basic("app", "s3cret")
    assert form(revoke) == {"token": ["rt"], "token_type_hint": ["refresh_token"]}


async def test_row_3_credential_fields_and_headers_with_issuer_audience():
    credential = FakeCredential()
    transport = FakeTransport(respond(200))

    await make_client(transport).revoke_token(
        "rt", credential=credential, metadata=METADATA
    )

    assert credential.calls == [(ISSUER, ISSUER)]
    (revoke,) = transport.requests
    assert revoke.headers["X-Client-Auth"] == "credential-header"
    assert form(revoke) == {
        "token": ["rt"],
        "token_type_hint": ["refresh_token"],
        "client_assertion": ["assertion"],
        "client_assertion_type": [JWT_BEARER],
    }


async def test_row_3_web_identity_assertion_audience_is_the_issuer(tmp_path):
    credential = WebIdentity(
        server_name="revoke", storage_dir=str(tmp_path), client_id="registered-client"
    )
    transport = FakeTransport(respond(200))

    await make_client(transport).revoke_token(
        "rt", credential=credential, metadata=METADATA
    )

    (revoke,) = transport.requests
    body = form(revoke)
    assert body["client_assertion_type"] == [JWT_BEARER]
    claims = jwt_claims(body["client_assertion"][0])
    assert claims["aud"] == ISSUER
    assert claims["iss"] == claims["sub"] == "registered-client"
    assert "client_id" not in body


async def test_row_3_workload_identity_sends_its_token_unchanged():
    credential = WorkloadIdentity(
        lambda: "platform-token", client_id="app-credential-id"
    )
    transport = FakeTransport(respond(200))

    await make_client(transport).revoke_token(
        "rt", credential=credential, metadata=METADATA
    )

    (revoke,) = transport.requests
    body = form(revoke)
    assert body["client_assertion"] == ["platform-token"]
    assert body["client_assertion_type"] == [JWT_BEARER]
    assert body["client_id"] == ["app-credential-id"]


@pytest.mark.parametrize(
    ("hint", "sent"),
    [
        ("access_token", "access_token"),
        ("refresh_token", "refresh_token"),
        ("custom_hint", "custom_hint"),
        (TokenTypeHint.ACCESS_TOKEN, "access_token"),
    ],
)
async def test_row_4_explicit_hint_is_sent_unchanged(hint, sent):
    transport = FakeTransport(respond(200))

    await make_client(transport).revoke_token(
        "tok", token_type_hint=hint, client_id="public-app", metadata=METADATA
    )

    assert form(transport.requests[0])["token_type_hint"] == [sent]


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(respond(200), id="empty-body"),
        pytest.param(respond(200, b"not json"), id="non-json-body"),
        pytest.param(respond(200, {"ignored": True}), id="json-body"),
    ],
)
async def test_row_5_http_200_succeeds_without_parsing_the_body(response):
    transport = FakeTransport(response)

    result = await make_client(transport).revoke_token(
        "unknown-or-known-token", client_id="public-app", metadata=METADATA
    )

    assert result is None
    assert len(transport.requests) == 1


async def test_row_6_missing_endpoint_in_metadata_is_config_error_without_request():
    transport = FakeTransport()

    with pytest.raises(ConfigError, match="revocation_endpoint"):
        await make_client(transport).revoke_token(
            "rt", client_id="public-app", metadata=NO_REVOCATION_METADATA
        )

    assert transport.requests == []


async def test_row_6_missing_endpoint_in_discovery_is_config_error_without_fallback():
    transport = FakeTransport(
        respond(200, {"issuer": ISSUER, "token_endpoint": TOKEN_ENDPOINT})
    )

    with pytest.raises(ConfigError, match="revocation_endpoint"):
        await discovering_client(transport).revoke_token("rt", client_id="public-app")

    assert [r.url for r in transport.requests] == [DISCOVERY_URL]


async def test_row_6_resolver_has_no_conventional_revocation_path():
    assert resolve_endpoints(ISSUER).revoke is None
    no_revocation = resolve_endpoints(
        ISSUER, discovered_metadata=NO_REVOCATION_METADATA
    )
    assert no_revocation.revoke is None
    assert (
        resolve_endpoints(ISSUER, discovered_metadata=METADATA).revoke
        == REVOCATION_ENDPOINT
    )


async def test_row_6_endpoint_override_wins_over_metadata():
    override = "https://zone.example.com/custom/revoke"
    transport = FakeTransport(respond(200))
    client = make_client(transport, endpoints=Endpoints(revoke=override))

    await client.revoke_token(
        "rt", client_id="public-app", metadata=NO_REVOCATION_METADATA
    )

    assert transport.requests[0].url == override


ERROR_CASES = [
    pytest.param(400, "invalid_request", True, id="invalid_request"),
    pytest.param(401, "invalid_client", False, id="invalid_client"),
    pytest.param(400, "invalid_grant", True, id="invalid_grant"),
    pytest.param(400, "unsupported_token_type", True, id="unsupported_token_type"),
    pytest.param(503, "temporarily_unavailable", True, id="temporarily_unavailable"),
    pytest.param(503, "invalid_client", True, id="503-with-permanent-code"),
    pytest.param(429, "access_denied", True, id="429-with-permanent-code"),
]


@pytest.mark.parametrize(("status", "code", "retryable"), ERROR_CASES)
async def test_row_7_oauth_error_is_typed_and_keeps_code_and_description(
    status, code, retryable
):
    transport = FakeTransport(
        respond(status, {"error": code, "error_description": f"{code} details"})
    )

    with pytest.raises(TokenRevocationError) as exc:
        await make_client(transport).revoke_token(
            "rt", client_id="public-app", metadata=METADATA
        )

    assert exc.value.error == code
    assert exc.value.error_description == f"{code} details"
    assert exc.value.status_code == status
    assert exc.value.retryable is retryable


async def test_row_8_transport_failure_is_retryable_without_hidden_retry():
    transport = FakeTransport(NetworkError(cause=httpx.ConnectError("refused")))

    with pytest.raises(NetworkError) as exc:
        await make_client(transport).revoke_token(
            "rt", client_id="public-app", metadata=METADATA
        )

    assert exc.value.retryable is True
    assert len(transport.requests) == 1


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_row_8_5xx_is_retryable_without_hidden_retry(status):
    transport = FakeTransport(respond(status, b"<html>down</html>"), respond(200))

    with pytest.raises(OAuthHttpError) as exc:
        await make_client(transport).revoke_token(
            "rt", client_id="public-app", metadata=METADATA
        )

    assert exc.value.status_code == status
    assert exc.value.retryable is True
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"credential": FakeCredential(), "client_id": "a"}, id="id"),
        pytest.param(
            {"credential": FakeCredential(), "client_secret": "s"}, id="secret"
        ),
        pytest.param({"credential": ExchangeOnlyCredential()}, id="no-client-auth"),
        pytest.param({"client_secret": "s"}, id="secret-without-id"),
        pytest.param({}, id="no-client-authentication"),
    ],
)
async def test_row_9_invalid_client_authentication_fails_before_request(kwargs):
    transport = FakeTransport()

    with pytest.raises(ConfigError):
        await make_client(transport).revoke_token("rt", metadata=METADATA, **kwargs)

    assert transport.requests == []


async def test_row_10_pre_discovered_metadata_avoids_discovery():
    transport = FakeTransport(respond(200))

    await discovering_client(transport).revoke_token(
        "rt", client_id="public-app", metadata=METADATA
    )

    assert [r.url for r in transport.requests] == [REVOCATION_ENDPOINT]


async def test_row_10_metadata_for_another_issuer_is_config_error():
    transport = FakeTransport()
    other = AuthorizationServerMetadata(
        issuer="https://other.example.com",
        token_endpoint=TOKEN_ENDPOINT,
        revocation_endpoint=REVOCATION_ENDPOINT,
    )

    with pytest.raises(ConfigError):
        await make_client(transport).revoke_token(
            "rt", client_id="public-app", metadata=other
        )

    assert transport.requests == []


async def test_row_11_empty_token_is_config_error_without_request():
    transport = FakeTransport()

    with pytest.raises(ConfigError, match="token"):
        await make_client(transport).revoke_token(
            "", client_id="public-app", metadata=METADATA
        )

    assert transport.requests == []


@pytest.mark.parametrize("code", ["invalid_grant", "unsupported_token_type"])
async def test_row_12_retryability_follows_the_base_table(code):
    transport = FakeTransport(respond(400, {"error": code}))

    with pytest.raises(TokenRevocationError) as exc:
        await make_client(transport).revoke_token(
            "rt", client_id="public-app", metadata=METADATA
        )

    assert code not in PERMANENT_ERROR_CODES
    assert exc.value.retryable is True


@pytest.mark.parametrize(
    ("row", "token", "hint"),
    [
        pytest.param(13, "exchanged-access-token-with-act", "access_token", id="13"),
        pytest.param(14, "access-token-without-sid", "access_token", id="14"),
        pytest.param(15, "jwt-from-another-issuer", "refresh_token", id="15"),
    ],
)
async def test_row_13_14_15_unsupported_token_type_is_typed(row, token, hint):
    transport = FakeTransport(
        respond(
            400,
            {"error": "unsupported_token_type", "error_description": f"row {row}"},
        )
    )

    with pytest.raises(TokenRevocationError) as exc:
        await make_client(transport).revoke_token(
            token, token_type_hint=hint, client_id="public-app", metadata=METADATA
        )

    assert exc.value.error == "unsupported_token_type"
    assert exc.value.error_description == f"row {row}"
    assert exc.value.status_code == 400
    assert form(transport.requests[0])["token"] == [token]
