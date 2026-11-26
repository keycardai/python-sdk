"""Unit rows from specs/oauth-client/token-introspection.md (version 1)."""

import base64
import json
from urllib.parse import parse_qs

import pytest

from keycardai.oauth import (
    AsyncClient,
    AuthorizationServerMetadata,
    BasicAuth,
    BearerAuth,
    Client,
    ClientAuthentication,
    ClientConfig,
    ConfigError,
    Endpoints,
    MissingTokenEndpointError,
    MultiZoneBasicAuth,
    NetworkError,
    OAuthHttpError,
    OAuthProtocolError,
    TokenIntrospection,
    TokenTypeHint,
)
from keycardai.oauth.client import resolve_endpoints
from keycardai.oauth.http._wire import HttpRequest, HttpResponse
from keycardai.oauth.server.credentials import (
    ClientSecret,
    WebIdentity,
    WorkloadIdentity,
)

ISSUER = "https://zone.example.com"
ENDPOINT = f"{ISSUER}/oauth/2/introspect"
DISCOVERY_URL = f"{ISSUER}/.well-known/oauth-authorization-server"
JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
METADATA = AuthorizationServerMetadata(issuer=ISSUER, introspection_endpoint=ENDPOINT)
CREDENTIAL = ClientSecret(("client", "secret"))

pytestmark = pytest.mark.asyncio


def response(body: dict | bytes, status: int = 200) -> HttpResponse:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return HttpResponse(status=status, headers={}, body=raw)


@pytest.fixture(params=[Client, AsyncClient])
def introspection(request):
    client_class = request.param

    def make_client(
        *responses, issuer=ISSUER, use_context=False, config=None, **kwargs
    ):
        requests: list[HttpRequest] = []
        timeouts: list[float | None] = []
        queued = iter(responses)

        def send(req, *, timeout=None):
            requests.append(req)
            timeouts.append(timeout)
            result = next(queued)
            if isinstance(result, Exception):
                raise result
            return result

        class SyncTransport:
            request_raw = staticmethod(send)

        class AsyncTransport:
            async def request_raw(self, req, *, timeout=None):
                return send(req, timeout=timeout)

        client = client_class(
            issuer,
            transport=SyncTransport() if client_class is Client else AsyncTransport(),
            config=config or ClientConfig(timeout=7.0),
            **kwargs,
        )

        async def introspect(*args, **kwargs):
            if use_context:
                if client_class is AsyncClient:
                    async with client:
                        return await client.introspect_token(*args, **kwargs)
                with client:
                    return client.introspect_token(*args, **kwargs)
            result = client.introspect_token(*args, **kwargs)
            if client_class is AsyncClient:
                return await result
            return result

        return introspect, requests, timeouts

    return make_client


def form(request: HttpRequest) -> dict[str, list[str]]:
    assert request.body is not None
    return parse_qs(request.body.decode(), keep_blank_values=True)


@pytest.mark.parametrize("use_context", [False, True])
async def test_row_1_client_secret_posts_basic_to_discovered_endpoint(
    introspection, use_context
):
    introspect, requests, timeouts = introspection(
        response({"issuer": ISSUER, "introspection_endpoint": ENDPOINT}),
        response({"active": False}),
        client_id="client",
        client_secret="secret",
        use_context=use_context,
    )

    await introspect("a +& token", timeout=2.5)

    discovery, post = requests
    assert (discovery.method, discovery.url) == ("GET", DISCOVERY_URL)
    assert "Authorization" not in discovery.headers
    assert (post.method, post.url) == ("POST", ENDPOINT)
    assert post.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert (
        post.headers["Authorization"]
        == BasicAuth("client", "secret").apply_headers()["Authorization"]
    )
    assert form(post) == {"token": ["a +& token"]}
    assert timeouts == [7.0, 2.5]


async def test_auto_registration_requires_token_endpoint_before_registering(introspection):
    introspect, requests, _ = introspection(
        response({"issuer": ISSUER, "introspection_endpoint": ENDPOINT}),
        use_context=True,
        config=ClientConfig(auto_register_client=True),
    )

    with pytest.raises(MissingTokenEndpointError):
        await introspect("token", credential=CREDENTIAL)

    assert [(request.method, request.url) for request in requests] == [
        ("GET", DISCOVERY_URL)
    ]


@pytest.mark.parametrize(
    "auth",
    [BasicAuth("client", "secret"), MultiZoneBasicAuth({ISSUER: ("client", "secret")})],
)
async def test_row_1_client_basic_auth_strategy_authenticates_introspection(
    introspection, auth
):
    introspect, requests, _ = introspection(response({"active": False}), auth=auth)

    await introspect("token", metadata=METADATA)

    (post,) = requests
    assert post.headers["Authorization"] == BasicAuth(
        "client", "secret"
    ).apply_headers()["Authorization"]
    assert form(post) == {"token": ["token"]}


async def test_row_2_private_jwt_authenticates_with_issuer_as_audience(
    introspection, tmp_path
):
    credential = WebIdentity(
        server_name="introspection", storage_dir=str(tmp_path), client_id="client"
    )

    class Credential:
        async def client_authentication(self, *, issuer, endpoint):
            assert issuer == endpoint == ISSUER
            auth = await credential.client_authentication(
                issuer=issuer, endpoint=endpoint
            )
            return ClientAuthentication(
                headers={"X-Client-Auth": "credential-header"},
                form_fields=auth.form_fields,
            )

    introspect, requests, _ = introspection(
        response({"active": False}), issuer=f"{ISSUER}/"
    )

    await introspect("token", credential=Credential(), metadata=METADATA)

    (post,) = requests
    assert post.headers["X-Client-Auth"] == "credential-header"
    fields = form(post)
    (assertion,) = fields.pop("client_assertion")
    payload = assertion.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["aud"] == ISSUER
    assert claims["iss"] == claims["sub"] == "client"
    assert fields == {"token": ["token"], "client_assertion_type": [JWT_BEARER]}


async def test_row_2_workload_assertion_is_forwarded_unchanged(introspection):
    introspect, requests, _ = introspection(response({"active": False}))
    credential = WorkloadIdentity(lambda: "workload-assertion", client_id="workload")

    await introspect("token", credential=credential, metadata=METADATA)

    assert form(requests[0]) == {
        "token": ["token"],
        "client_id": ["workload"],
        "client_assertion": ["workload-assertion"],
        "client_assertion_type": [JWT_BEARER],
    }


@pytest.mark.parametrize("audience", ["resource1", ["resource1", "resource2"]])
async def test_row_3_active_access_token_returns_typed_claims(introspection, audience):
    claims = {
        "active": True,
        "token_type": "Bearer",
        "iss": ISSUER,
        "sub": "subject",
        "client_id": "client",
        "aud": audience,
        "scope": "read write",
        "sid": "session",
        "jti": "identifier",
        "iat": 100,
        "exp": 200,
    }
    introspect, _, _ = introspection(response(claims))

    result = await introspect("token", credential=CREDENTIAL, metadata=METADATA)

    assert isinstance(result, TokenIntrospection)
    assert result.model_dump() == {**claims, "scope": ["read", "write"]}


async def test_row_4_inactive_token_is_a_result(introspection):
    introspect, _, _ = introspection(response({"active": False}))

    result = await introspect("token", credential=CREDENTIAL, metadata=METADATA)

    assert result.active is False
    assert result.model_dump(exclude_unset=True) == {"active": False}


@pytest.mark.parametrize("supplied", [False, True])
async def test_row_5_no_introspection_endpoint_is_config_error(introspection, supplied):
    metadata = AuthorizationServerMetadata(issuer=ISSUER)
    assert resolve_endpoints(ISSUER, discovered_metadata=metadata).introspect is None
    introspect, requests, _ = introspection(
        response({"issuer": ISSUER}), endpoints=Endpoints(introspect=ENDPOINT)
    )

    with pytest.raises(ConfigError, match="introspection_endpoint"):
        await introspect(
            "token", credential=CREDENTIAL, metadata=metadata if supplied else None
        )

    assert [request.url for request in requests] == (
        [] if supplied else [DISCOVERY_URL]
    )


@pytest.mark.parametrize("supplied", [False, True])
async def test_plaintext_endpoint_is_rejected_before_post(introspection, supplied):
    endpoint = "http://zone.example.com/oauth/2/introspect"
    metadata = AuthorizationServerMetadata(issuer=ISSUER, introspection_endpoint=endpoint)
    introspect, requests, _ = introspection(
        response({"issuer": ISSUER, "introspection_endpoint": endpoint})
    )

    with pytest.raises(ConfigError, match="not an HTTPS URL"):
        await introspect(
            "token", credential=CREDENTIAL, metadata=metadata if supplied else None
        )

    assert [request.url for request in requests] == ([] if supplied else [DISCOVERY_URL])


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [(401, "invalid_client", False), (400, "invalid_request", True)],
)
async def test_row_6_oauth_error_preserves_response_fields(
    introspection, status, code, retryable
):
    introspect, _, _ = introspection(
        response(
            {"error": code, "error_description": "description", "reason": "cause"},
            status,
        )
    )

    with pytest.raises(OAuthProtocolError) as caught:
        await introspect("token", credential=CREDENTIAL, metadata=METADATA)

    error = caught.value
    assert (error.error, error.error_description, error.reason) == (
        code,
        "description",
        "cause",
    )
    assert error.status_code == status
    assert error.retryable is retryable


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        (NetworkError(TimeoutError("timed out")), NetworkError),
        (response({"error": "server_error"}, 500), OAuthProtocolError),
        (response(b"service unavailable", 503), OAuthHttpError),
    ],
)
async def test_row_7_transient_failure_is_retryable_without_retry(
    introspection, failure, error_type
):
    introspect, requests, _ = introspection(failure)

    with pytest.raises(error_type) as caught:
        await introspect("token", credential=CREDENTIAL, metadata=METADATA)

    assert caught.value.retryable is True
    assert len(requests) == 1


@pytest.mark.parametrize(
    "body", [b"", b"{", b"[]", {}, {"active": 1}, {"active": "false"}, {"active": None}]
)
async def test_row_8_malformed_body_or_active_is_invalid_response(introspection, body):
    introspect, _, _ = introspection(response(body))

    with pytest.raises(OAuthProtocolError) as caught:
        await introspect("token", credential=CREDENTIAL, metadata=METADATA)

    assert caught.value.error == "invalid_response"
    assert caught.value.status_code == 200
    assert caught.value.retryable is True


async def test_row_9_results_not_cached_but_discovery_is(introspection):
    introspect, requests, _ = introspection(
        response({"issuer": ISSUER, "introspection_endpoint": ENDPOINT}),
        response({"active": True}),
        response({"active": False}),
    )

    assert (await introspect("token", credential=CREDENTIAL)).active is True
    assert (await introspect("token", credential=CREDENTIAL)).active is False
    assert [request.url for request in requests] == [DISCOVERY_URL, ENDPOINT, ENDPOINT]


async def test_row_10_supplied_metadata_skips_discovery(introspection):
    introspect, requests, _ = introspection(response({"active": False}))

    await introspect("token", credential=CREDENTIAL, metadata=METADATA)

    assert [request.url for request in requests] == [ENDPOINT]


async def test_row_10_metadata_issuer_mismatch_fails_before_request(introspection):
    introspect, requests, _ = introspection(issuer="https://other.example.com")

    with pytest.raises(ConfigError, match="does not match"):
        await introspect("token", credential=CREDENTIAL, metadata=METADATA)

    assert requests == []


class PublicCredential:
    async def client_authentication(self, *, issuer, endpoint):
        return ClientAuthentication(form_fields={"client_id": "public"})


@pytest.mark.parametrize("credential", [None, object(), PublicCredential()])
async def test_row_11_client_authentication_required_before_discovery(
    introspection, credential
):
    introspect, requests, _ = introspection(client_id="public")

    with pytest.raises(ConfigError):
        await introspect("token", credential=credential)

    assert requests == []


@pytest.mark.parametrize(
    "auth",
    [
        BearerAuth("bearer-token"),
        MultiZoneBasicAuth({"https://other.example.com": ("client", "secret")}),
    ],
)
async def test_row_11_unusable_auth_strategy_fails_before_discovery(introspection, auth):
    introspect, requests, _ = introspection(auth=auth)

    with pytest.raises(ConfigError):
        await introspect("token")

    assert requests == []


@pytest.mark.parametrize("args", [(), ("",)])
async def test_row_12_missing_or_empty_token_fails_before_request(introspection, args):
    introspect, requests, _ = introspection()

    with pytest.raises(ConfigError, match="token"):
        await introspect(*args, credential=CREDENTIAL)

    assert requests == []


async def test_row_13_unknown_claims_are_preserved(introspection):
    extra = {"act": {"sub": "actor"}, "sub_profile": "user", "auth_time": 50}
    introspect, _, _ = introspection(response({"active": True, "iss": ISSUER, **extra}))

    result = await introspect("token", credential=CREDENTIAL, metadata=METADATA)

    assert result.iss == ISSUER
    assert result.model_extra == extra


async def test_row_14_active_refresh_token_has_no_token_type(introspection):
    introspect, _, _ = introspection(response({"active": True, "sid": "session"}))

    result = await introspect("token", credential=CREDENTIAL, metadata=METADATA)

    assert result.active is True
    assert result.token_type is None
    assert "token_type" not in result.model_dump(exclude_unset=True)


@pytest.mark.parametrize(
    ("hint", "sent"),
    [
        (None, None),
        ("Custom + hint", "Custom + hint"),
        ("", ""),
        (TokenTypeHint.REFRESH_TOKEN, "refresh_token"),
    ],
)
async def test_row_15_optional_hint_passes_through(introspection, hint, sent):
    introspect, requests, _ = introspection(response({"active": False}))

    await introspect(
        "token", credential=CREDENTIAL, metadata=METADATA, token_type_hint=hint
    )

    expected = {"token": ["token"]}
    if sent is not None:
        expected["token_type_hint"] = [sent]
    assert form(requests[0]) == expected
