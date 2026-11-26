"""Device authorization grant (RFC 8628) on AsyncClient.

Test names start with the row of the unit test table in the Keycard SDK spec
(specs/oauth-client/device-authorization.md). The tests run against a fake
transport and a fake clock. The fake clock records each wait and advances its
time by it. Row 28 alone uses a real local server and httpx.
"""

import asyncio
import base64
import dataclasses
import json
import re
import warnings
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from keycardai.oauth import (
    AsyncClient,
    AuthorizationServerDiscoveryError,
    AuthorizationServerMetadata,
    ClientAuthentication,
    ClientConfig,
    ConfigError,
    DeviceAuthorization,
    DeviceAuthorizationError,
    DevicePollError,
    Endpoints,
    LegacyGrantCredentialWarning,
    MissingTokenEndpointError,
    NetworkError,
    OAuthHttpError,
    ResponseBodyTooLargeError,
    TokenResponse,
)
from keycardai.oauth.http._transports import ResponseBodyDecodeError
from keycardai.oauth.http._wire import HttpRequest, HttpResponse
from keycardai.oauth.server.credentials import WebIdentity, WorkloadIdentity
from keycardai.oauth.server.exceptions import WorkloadIdentityRuntimeError

ISSUER = "https://zone.example.com"
DISCOVERY_URL = f"{ISSUER}/.well-known/oauth-authorization-server"
DEVICE_ENDPOINT = "https://zone.example.com/oauth/device_authorization"
TOKEN_ENDPOINT = "https://zone.example.com/oauth/token"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

pytestmark = pytest.mark.asyncio

METADATA = AuthorizationServerMetadata(
    issuer=ISSUER,
    token_endpoint=TOKEN_ENDPOINT,
    device_authorization_endpoint=DEVICE_ENDPOINT,
)

PENDING = {"error": "authorization_pending"}
SLOW_DOWN = {"error": "slow_down"}
TOKEN_BODY = {
    "access_token": "at",
    "token_type": "Bearer",
    "expires_in": 3600,
    "refresh_token": "rt",
    "id_token": "header.claims.sig",
    "scope": "openid",
}


class FakeClock:
    def __init__(self, block: asyncio.Event | None = None):
        self.current = T0
        self.sleeps: list[float] = []
        self.block = block

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.block is not None:
            await self.block.wait()
        self.current += timedelta(seconds=seconds)


class FakeTransport:
    """Answers each request with the next queued response or exception."""

    def __init__(self, *responses, block: asyncio.Event | None = None):
        self.responses = list(responses)
        self.requests: list[HttpRequest] = []
        self.timeouts: list[float | None] = []
        self.block = block

    async def request_raw(
        self, req: HttpRequest, *, timeout: float | None = None
    ) -> HttpResponse:
        self.requests.append(req)
        self.timeouts.append(timeout)
        if self.block is not None:
            await self.block.wait()
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeCredential:
    """A ClientAuthenticator that numbers each assertion it returns."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def client_authentication(
        self, *, issuer: str, endpoint: str
    ) -> ClientAuthentication:
        self.calls.append((issuer, endpoint))
        return ClientAuthentication(
            headers={"X-Client-Auth": "credential-header"},
            form_fields={
                "client_assertion": f"assertion-{len(self.calls)}",
                "client_assertion_type": JWT_BEARER,
            },
        )


class ExchangeOnlyCredential:
    """A credential with token exchange and no client authentication."""

    async def prepare_token_exchange_request(self, client, subject_token, resource, auth_info=None):
        raise AssertionError("not used")


def respond(status: int, body: dict | bytes) -> HttpResponse:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return HttpResponse(status=status, headers={}, body=raw)


def undecodable(status: int) -> ResponseBodyDecodeError:
    """What the httpx transport raises for a gzip body that is not gzip."""
    return ResponseBodyDecodeError(
        status=status,
        headers={"content-encoding": "gzip"},
        cause=httpx.DecodingError("incorrect header check"),
        operation="POST",
    )


def device_response(**overrides) -> HttpResponse:
    body = {
        "device_code": "dc",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://zone.example.com/device",
        "verification_uri_complete": "https://zone.example.com/device?user_code=ABCD-EFGH",
        "expires_in": 600,
        "interval": 5,
    }
    body.update(overrides)
    return respond(200, {k: v for k, v in body.items() if v is not None})


def authorization(expires_in: int = 600, interval: int = 5) -> DeviceAuthorization:
    return DeviceAuthorization(
        device_code="dc",
        user_code="ABCD-EFGH",
        verification_uri="https://zone.example.com/device",
        expires_in=expires_in,
        interval=interval,
        expires_at=T0 + timedelta(seconds=expires_in),
    )


def make_client(transport: FakeTransport, clock: FakeClock | None = None) -> AsyncClient:
    return AsyncClient(
        ISSUER,
        transport=transport,
        clock=clock or FakeClock(),
        config=ClientConfig(
            enable_metadata_discovery=False, auto_register_client=False, timeout=7.0
        ),
    )


def form(req: HttpRequest) -> dict[str, list[str]]:
    assert req.body is not None
    return parse_qs(req.body.decode())


def jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def exactly(message: str) -> str:
    return f"^{re.escape(message)}$"


def request_op(client: AsyncClient, metadata=METADATA, **auth):
    return client.request_device_authorization(metadata=metadata, **auth)


def poll_once_op(client: AsyncClient, metadata=METADATA, **auth):
    return client.poll_device_token(authorization(), metadata=metadata, **auth)


def poll_until_done_op(client: AsyncClient, metadata=METADATA, **auth):
    return client.wait_for_device_token(authorization(), metadata=metadata, **auth)


OPERATION_NAMES = {
    request_op: "POST /device_authorization",
    poll_once_op: "POST /token (device_code)",
}

SINGLE_REQUEST_OPERATIONS = [
    pytest.param(request_op, id="request"),
    pytest.param(poll_once_op, id="poll-once"),
]
OPERATIONS = [
    *SINGLE_REQUEST_OPERATIONS,
    pytest.param(poll_until_done_op, id="poll-until-done"),
]


FORM_HEADERS = {
    "User-Agent": ClientConfig().user_agent,
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
}


# Device authorization request


async def test_row_1_credential_scopes_and_two_resources():
    transport = FakeTransport(device_response())
    credential = FakeCredential()

    await make_client(transport).request_device_authorization(
        credential=credential,
        scopes=["openid", "profile"],
        resources=["https://a.example.com", "https://b.example.com"],
        metadata=METADATA,
    )

    [req] = transport.requests
    assert (req.method, req.url) == ("POST", DEVICE_ENDPOINT)
    assert req.headers == {**FORM_HEADERS, "X-Client-Auth": "credential-header"}
    assert form(req) == {
        "scope": ["openid profile"],
        "resource": ["https://a.example.com", "https://b.example.com"],
        "client_assertion": ["assertion-1"],
        "client_assertion_type": [JWT_BEARER],
    }
    assert credential.calls == [(ISSUER, DEVICE_ENDPOINT)]
    assert transport.timeouts == [7.0]


async def test_row_2_public_client_id():
    transport = FakeTransport(device_response())

    await make_client(transport).request_device_authorization(
        client_id="public-app", metadata=METADATA
    )

    [req] = transport.requests
    assert form(req) == {"client_id": ["public-app"]}
    assert req.headers == FORM_HEADERS


@pytest.mark.parametrize(
    ("auth", "message"),
    [
        pytest.param(
            {"credential": FakeCredential(), "client_id": "public-app"},
            "Pass exactly one of `credential` and `client_id`.",
            id="row-3-both",
        ),
        pytest.param(
            {},
            "Configure `credential` or a public `client_id` on the client.",
            id="row-3-neither",
        ),
        pytest.param({"client_id": ""}, "`client_id` is empty.", id="empty-client_id"),
        pytest.param(
            {"credential": ExchangeOnlyCredential()},
            "ExchangeOnlyCredential does not provide client authentication",
            id="row-23-no-client-authentication",
        ),
        pytest.param(
            {"credential": WorkloadIdentity(lambda: "platform-token")},
            "WorkloadIdentity: client_id is required for interactive grants "
            "(the public ID of a v2 token credential)",
            id="workload-identity-without-client_id",
        ),
    ],
)
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_rows_3_and_23_client_authentication_misconfigured(operation, auth, message):
    transport = FakeTransport()
    clock = FakeClock()

    with pytest.raises(ConfigError, match=exactly(message)):
        await operation(make_client(transport, clock), **auth)

    assert transport.requests == []
    assert clock.sleeps == []


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_row_3_checks_run_before_discovery(operation):
    transport = FakeTransport()
    client = AsyncClient(ISSUER, transport=transport, clock=FakeClock())

    with pytest.raises(
        ConfigError,
        match=exactly("Configure `credential` or a public `client_id` on the client."),
    ):
        await operation(client, None)

    assert transport.requests == []


def metadata(**endpoints) -> AuthorizationServerMetadata:
    return AuthorizationServerMetadata(issuer=ISSUER, **endpoints)


@pytest.mark.parametrize(
    ("operation", "bad_metadata", "message"),
    [
        pytest.param(
            request_op,
            metadata(token_endpoint=TOKEN_ENDPOINT),
            f"Authorization server {ISSUER} does not advertise a "
            "device_authorization_endpoint",
            id="row-4-no-device-endpoint",
        ),
        pytest.param(
            poll_once_op,
            metadata(device_authorization_endpoint=DEVICE_ENDPOINT),
            f"Authorization server {ISSUER} does not advertise a token_endpoint",
            id="row-30-no-token-endpoint",
        ),
        pytest.param(
            poll_until_done_op,
            metadata(device_authorization_endpoint=DEVICE_ENDPOINT),
            f"Authorization server {ISSUER} does not advertise a token_endpoint",
            id="row-30-no-token-endpoint-poll-until-done",
        ),
        pytest.param(
            poll_until_done_op,
            metadata(
                token_endpoint="file:///etc/token",
                device_authorization_endpoint=DEVICE_ENDPOINT,
            ),
            f"Authorization server {ISSUER} has a token_endpoint that is not an "
            "http or https URL: 'file:///etc/token'",
            id="row-30-token-endpoint-not-http-poll-until-done",
        ),
        pytest.param(
            request_op,
            metadata(
                token_endpoint=TOKEN_ENDPOINT,
                device_authorization_endpoint="ftp://zone.example.com/device",
            ),
            f"Authorization server {ISSUER} has a device_authorization_endpoint "
            "that is not an http or https URL: 'ftp://zone.example.com/device'",
            id="row-30-device-endpoint-not-http",
        ),
        pytest.param(
            poll_once_op,
            metadata(
                token_endpoint="/oauth/token",
                device_authorization_endpoint=DEVICE_ENDPOINT,
            ),
            f"Authorization server {ISSUER} has a token_endpoint that is not an "
            "http or https URL: '/oauth/token'",
            id="row-30-token-endpoint-relative",
        ),
        pytest.param(
            poll_once_op,
            metadata(
                token_endpoint="https://[bad", device_authorization_endpoint=DEVICE_ENDPOINT
            ),
            f"Authorization server {ISSUER} has a token_endpoint that is not an "
            "http or https URL: 'https://[bad'",
            id="row-30-token-endpoint-does-not-parse",
        ),
        pytest.param(
            request_op,
            metadata(token_endpoint=TOKEN_ENDPOINT, device_authorization_endpoint=123),
            f"Authorization server {ISSUER} has a device_authorization_endpoint "
            "that is not an http or https URL: 123",
            id="row-30-device-endpoint-not-a-string",
        ),
        pytest.param(
            poll_once_op,
            metadata(token_endpoint=[TOKEN_ENDPOINT], device_authorization_endpoint=DEVICE_ENDPOINT),
            f"Authorization server {ISSUER} has a token_endpoint that is not an "
            f"http or https URL: ['{TOKEN_ENDPOINT}']",
            id="row-30-token-endpoint-a-list",
        ),
    ],
)
async def test_rows_4_and_30_endpoint_missing_or_not_http(operation, bad_metadata, message):
    transport = FakeTransport()
    clock = FakeClock()
    credential = FakeCredential()

    with pytest.raises(ConfigError, match=exactly(message)):
        await operation(make_client(transport, clock), bad_metadata, credential=credential)

    assert transport.requests == []
    assert credential.calls == []
    assert clock.sleeps == []


async def test_row_30_request_checks_only_the_endpoint_it_sends_to():
    transport = FakeTransport(device_response())

    result = await make_client(transport).request_device_authorization(
        client_id="public-app",
        metadata=metadata(device_authorization_endpoint=DEVICE_ENDPOINT),
    )

    assert result.device_code == "dc"


async def test_row_4_discovered_metadata_without_device_endpoint():
    transport = FakeTransport(
        respond(200, {"issuer": ISSUER, "token_endpoint": TOKEN_ENDPOINT})
    )
    client = AsyncClient(ISSUER, transport=transport, clock=FakeClock())

    with pytest.raises(
        ConfigError,
        match=exactly(
            f"Authorization server {ISSUER} does not advertise a "
            "device_authorization_endpoint"
        ),
    ):
        await client.request_device_authorization(client_id="public-app")

    assert [(r.method, r.url) for r in transport.requests] == [("GET", DISCOVERY_URL)]


DISCOVERED_WITHOUT_DEVICE = respond(200, {"issuer": ISSUER, "token_endpoint": TOKEN_ENDPOINT})
DISCOVERED = respond(
    200,
    {
        "issuer": ISSUER,
        "token_endpoint": TOKEN_ENDPOINT,
        "device_authorization_endpoint": DEVICE_ENDPOINT,
    },
)


async def test_row_4_failed_endpoint_check_is_remembered_for_negative_ttl(monkeypatch):
    """Base spec, Metadata failures are not sticky: a deterministic failure is
    remembered for at most negative_ttl (60 seconds here), although the
    metadata itself is cached for discovery_ttl."""
    now = [1000.0]
    monkeypatch.setattr("keycardai.oauth._metadata_cache._now", lambda: now[0])
    transport = FakeTransport(DISCOVERED_WITHOUT_DEVICE, DISCOVERED, device_response())
    client = AsyncClient(ISSUER, transport=transport, clock=FakeClock())

    for elapsed in (0, 59):
        now[0] = 1000.0 + elapsed
        with pytest.raises(ConfigError, match="does not advertise a device_authorization_endpoint"):
            await client.request_device_authorization(client_id="public-app")
    now[0] = 1060.0
    result = await client.request_device_authorization(client_id="public-app")

    assert result.device_code == "dc"
    assert [(r.method, r.url) for r in transport.requests] == [
        ("GET", DISCOVERY_URL),
        ("GET", DISCOVERY_URL),
        ("POST", DEVICE_ENDPOINT),
    ]


async def fail_device_check_then_rediscover(monkeypatch, rediscovery, *later):
    """Fail the device endpoint check on discovered metadata, then fail the
    discovery that follows once negative_ttl (60 seconds) has passed.

    Returns the client, its transport, and the rediscovery error.
    """
    now = [1000.0]
    monkeypatch.setattr("keycardai.oauth._metadata_cache._now", lambda: now[0])
    transport = FakeTransport(DISCOVERED_WITHOUT_DEVICE, rediscovery, *later)
    client = AsyncClient(ISSUER, transport=transport, clock=FakeClock())

    with pytest.raises(ConfigError, match="does not advertise a device_authorization_endpoint"):
        await client.request_device_authorization(client_id="public-app")
    now[0] = 1060.0
    with pytest.raises(AuthorizationServerDiscoveryError) as exc:
        await client.request_device_authorization(client_id="public-app")

    assert [(r.method, r.url) for r in transport.requests] == [
        ("GET", DISCOVERY_URL),
        ("GET", DISCOVERY_URL),
    ]
    return client, transport, exc.value


async def test_transient_rediscovery_failure_keeps_cached_metadata(monkeypatch):
    """A transient failure doesn't replace still-fresh metadata."""
    client, transport, error = await fail_device_check_then_rediscover(
        monkeypatch, respond(503, {}), respond(200, TOKEN_BODY)
    )

    token = await client.poll_device_token(authorization(), client_id="public-app")

    assert error.retryable is True
    assert token.access_token == "at"
    assert [(r.method, r.url) for r in transport.requests][2:] == [("POST", TOKEN_ENDPOINT)]


async def test_row_37_discovery_retry_after(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("keycardai.oauth._metadata_cache._now", lambda: now[0])
    transport = FakeTransport(
        HttpResponse(503, {"Retry-After": "5"}, b""),
        respond(200, {
            "issuer": ISSUER,
            "token_endpoint": TOKEN_ENDPOINT,
            "device_authorization_endpoint": DEVICE_ENDPOINT,
        }),
        respond(200, {
            "device_code": "device",
            "user_code": "user",
            "verification_uri": f"{ISSUER}/verify",
            "expires_in": 60,
        }),
    )
    client = AsyncClient(ISSUER, transport=transport, clock=FakeClock())
    with pytest.raises(AuthorizationServerDiscoveryError) as first:
        await client.request_device_authorization(client_id="public-app")
    now[0] += 4.9
    with pytest.raises(AuthorizationServerDiscoveryError) as gated:
        await client.request_device_authorization(client_id="public-app")
    assert gated.value is first.value
    assert len(transport.requests) == 1
    now[0] += 0.11
    result = await client.request_device_authorization(client_id="public-app")
    assert result.device_code == "device"
    assert [(r.method, r.url) for r in transport.requests] == [
        ("GET", DISCOVERY_URL),
        ("GET", DISCOVERY_URL),
        ("POST", DEVICE_ENDPOINT),
    ]


async def test_deterministic_rediscovery_failure_replaces_cached_metadata(monkeypatch):
    """Base spec: a deterministic failure is remembered for negative_ttl. It
    replaces the cached metadata, so the next operation raises it without a
    request."""
    client, transport, error = await fail_device_check_then_rediscover(
        monkeypatch, respond(404, {})
    )

    with pytest.raises(AuthorizationServerDiscoveryError) as exc:
        await client.poll_device_token(authorization(), client_id="public-app")

    assert error.retryable is False
    assert exc.value is error
    assert len(transport.requests) == 2


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_row_30_discovered_metadata_without_token_endpoint(operation):
    """Discovery on AsyncClient requires token_endpoint for every operation.
    The error is a ConfigError, as row 30 requires, and a discovery failure."""
    transport = FakeTransport(
        respond(200, {"issuer": ISSUER, "device_authorization_endpoint": DEVICE_ENDPOINT})
    )
    client = AsyncClient(ISSUER, transport=transport, clock=FakeClock())

    with pytest.raises(
        MissingTokenEndpointError,
        match=exactly(f"Authorization server {ISSUER} does not advertise a token_endpoint"),
    ) as exc:
        await operation(client, None, client_id="public-app")

    assert isinstance(exc.value, ConfigError)
    assert isinstance(exc.value, AuthorizationServerDiscoveryError)
    assert exc.value.retryable is False
    assert [(r.method, r.url) for r in transport.requests] == [("GET", DISCOVERY_URL)]


async def test_discovered_endpoints_are_used():
    transport = FakeTransport(DISCOVERED, device_response(), respond(200, TOKEN_BODY))
    client = AsyncClient(ISSUER, transport=transport, clock=FakeClock())

    auth = await client.request_device_authorization(client_id="public-app")
    await client.poll_device_token(auth, client_id="public-app")

    assert [(r.method, r.url) for r in transport.requests] == [
        ("GET", DISCOVERY_URL),
        ("POST", DEVICE_ENDPOINT),
        ("POST", TOKEN_ENDPOINT),
    ]


# A token endpoint override wins over every source of metadata.
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(None, id="no-metadata"),
        pytest.param(METADATA, id="metadata-with-token-endpoint"),
        pytest.param(
            metadata(device_authorization_endpoint=DEVICE_ENDPOINT),
            id="metadata-without-token-endpoint",
        ),
    ],
)
async def test_token_endpoint_override_wins(source):
    override = "https://override.example.com/token"
    transport = FakeTransport(respond(200, TOKEN_BODY))
    client = AsyncClient(
        ISSUER,
        transport=transport,
        endpoints=Endpoints(token=override),
        clock=FakeClock(),
        config=ClientConfig(enable_metadata_discovery=False),
    )

    await client.poll_device_token(authorization(), client_id="public-app", metadata=source)

    assert [r.url for r in transport.requests] == [override]


@pytest.mark.parametrize(
    ("operation", "response"),
    [
        pytest.param(request_op, device_response(), id="request"),
        pytest.param(poll_once_op, respond(200, TOKEN_BODY), id="poll-once"),
    ],
)
async def test_requests_send_the_configured_user_agent_and_custom_headers(operation, response):
    transport = FakeTransport(response)
    client = AsyncClient(
        ISSUER,
        transport=transport,
        clock=FakeClock(),
        config=ClientConfig(
            enable_metadata_discovery=False,
            user_agent="test-agent/1.0",
            custom_headers={"X-Tenant": "t1", "X-Client-Auth": "from-config"},
        ),
    )

    await operation(client, credential=FakeCredential())

    [req] = transport.requests
    assert req.headers == {
        **FORM_HEADERS,
        "User-Agent": "test-agent/1.0",
        "X-Tenant": "t1",
        "X-Client-Auth": "credential-header",
    }


@pytest.mark.parametrize(
    ("operation", "endpoint"),
    [
        pytest.param(request_op, "device_authorization_endpoint", id="request"),
        pytest.param(poll_once_op, "token_endpoint", id="poll-once"),
    ],
)
async def test_no_conventional_endpoint_without_discovery(operation, endpoint):
    """The spec forbids a conventional path such as {issuer}/oauth/token."""
    transport = FakeTransport()
    client = make_client(transport)

    with pytest.raises(
        ConfigError, match=exactly(f"Authorization server {ISSUER} does not advertise a {endpoint}")
    ):
        await operation(client, None, client_id="public-app")

    assert transport.requests == []


async def test_row_5_interval_defaults_and_expires_at_from_clock():
    clock = FakeClock()
    clock.current = T0 + timedelta(seconds=42)
    transport = FakeTransport(device_response(interval=None, expires_in=900))

    result = await make_client(transport, clock).request_device_authorization(
        client_id="public-app", metadata=METADATA
    )

    assert result == DeviceAuthorization(
        device_code="dc",
        user_code="ABCD-EFGH",
        verification_uri="https://zone.example.com/device",
        verification_uri_complete="https://zone.example.com/device?user_code=ABCD-EFGH",
        expires_in=900,
        interval=5,
        expires_at=T0 + timedelta(seconds=42 + 900),
    )


async def test_row_38_device_code_is_left_out_of_printed_forms_and_kept_in_json():
    value = authorization()

    assert "device_code" not in repr(value)
    assert "'dc'" not in repr(value)
    for printed in (repr(value), str(value)):
        assert "device_code" not in printed
        assert "'dc'" not in printed
        for name in (
            "user_code", "verification_uri", "verification_uri_complete",
            "expires_in", "interval", "expires_at",
        ):
            assert f"{name}=" in printed
    assert value.model_dump()["device_code"] == "dc"
    assert DeviceAuthorization.model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize(
    ("response", "description"),
    [
        pytest.param(
            device_response(user_code=None),
            "Device authorization response is missing user_code",
            id="row-6-user_code",
        ),
        pytest.param(
            device_response(device_code=None),
            "Device authorization response is missing device_code",
            id="device_code",
        ),
        pytest.param(
            device_response(verification_uri=None),
            "Device authorization response is missing verification_uri",
            id="verification_uri",
        ),
        pytest.param(
            device_response(expires_in=None),
            "Device authorization response is missing expires_in",
            id="expires_in",
        ),
        pytest.param(
            device_response(user_code=""),
            "Device authorization response is missing user_code",
            id="empty-user_code",
        ),
        pytest.param(
            device_response(user_code=7),
            "Device authorization response has an invalid user_code",
            id="user_code-not-a-string",
        ),
        *[
            pytest.param(
                device_response(expires_in=value),
                "Device authorization response has an invalid expires_in",
                id=f"expires_in-{value!r}",
            )
            for value in (0, -1, "600", True, 1.5, 10**12)
        ],
        pytest.param(
            device_response(interval=0),
            "Device authorization response has an invalid interval",
            id="row-25-zero",
        ),
        pytest.param(
            device_response(interval=-1),
            "Device authorization response has an invalid interval",
            id="row-25-negative",
        ),
        pytest.param(
            device_response(interval="5"),
            "Device authorization response has an invalid interval",
            id="row-25-string",
        ),
        pytest.param(
            device_response(interval=1.5),
            "Device authorization response has an invalid interval",
            id="fraction",
        ),
        pytest.param(
            device_response(interval=True),
            "Device authorization response has an invalid interval",
            id="boolean",
        ),
        pytest.param(
            respond(200, b"[]"), "The response body is not a JSON object", id="json-array"
        ),
        pytest.param(
            respond(200, b"<html>"), "The response body is not a JSON object", id="not-json"
        ),
        pytest.param(
            respond(200, b"[" * 100_000),
            "The response body is not a JSON object",
            id="nested-too-deep",
        ),
    ],
)
async def test_rows_6_and_25_malformed_device_authorization_response(response, description):
    transport = FakeTransport(response)

    with pytest.raises(DeviceAuthorizationError) as exc:
        await make_client(transport).request_device_authorization(
            client_id="public-app", metadata=METADATA
        )

    assert exc.value.error == "invalid_response"
    assert exc.value.error_description == description
    assert exc.value.status_code == 200
    assert exc.value.retryable is True


async def test_verification_uri_complete_that_is_not_a_string_is_dropped():
    transport = FakeTransport(device_response(verification_uri_complete=["x"]))

    result = await make_client(transport).request_device_authorization(
        client_id="public-app", metadata=METADATA
    )

    assert result.verification_uri_complete is None


# Error responses for both operations: error type, status, code, and retryable.
ERROR_RESPONSES = [
    pytest.param(
        respond(400, {"error": "invalid_target", "error_description": "no resource"}),
        DeviceAuthorizationError,
        400,
        "invalid_target",
        True,
        id="oauth-error",
    ),
    pytest.param(
        respond(401, {"error": "invalid_client"}),
        DeviceAuthorizationError,
        401,
        "invalid_client",
        False,
        id="invalid_client",
    ),
    pytest.param(
        respond(503, {"error": "access_denied"}),
        DeviceAuthorizationError,
        503,
        "access_denied",
        True,
        id="row-29-503-with-permanent-code",
    ),
    pytest.param(
        respond(429, {"error": "invalid_grant"}),
        DeviceAuthorizationError,
        429,
        "invalid_grant",
        True,
        id="429-with-invalid_grant",
    ),
    pytest.param(respond(502, b"bad gateway"), OAuthHttpError, 502, None, True, id="5xx"),
    pytest.param(respond(429, b""), OAuthHttpError, 429, None, True, id="429"),
    pytest.param(respond(404, b"not found"), OAuthHttpError, 404, None, False, id="4xx"),
    pytest.param(
        respond(302, TOKEN_BODY), OAuthHttpError, 302, None, False, id="3xx-is-not-success"
    ),
    pytest.param(
        undecodable(200),
        DeviceAuthorizationError,
        200,
        "invalid_response",
        True,
        id="row-31-200-body-does-not-decode",
    ),
    pytest.param(
        undecodable(503), OAuthHttpError, 503, None, True, id="503-body-does-not-decode"
    ),
]


@pytest.mark.parametrize(
    ("operation", "description"),
    [
        pytest.param(
            request_op,
            "Device authorization response is missing device_code, user_code, "
            "verification_uri, expires_in",
            id="request",
        ),
        pytest.param(
            poll_once_op, "Device token response is missing access_token", id="poll-once"
        ),
    ],
)
async def test_2xx_with_an_error_field_is_a_success_response(operation, description):
    """Spec, Poll once: a 2xx response returns the token. RFC 6749 Section
    5.2 sends an OAuth error with 400, or 401 for invalid_client, so an
    error field on a 2xx is a malformed success response."""
    transport = FakeTransport(respond(200, {"error": "authorization_pending"}))

    with pytest.raises(DeviceAuthorizationError) as exc:
        await operation(make_client(transport), client_id="public-app")

    assert exc.value.error == "invalid_response"
    assert exc.value.error_description == description
    assert exc.value.status_code == 200
    assert exc.value.retryable is True


async def test_poll_once_returns_a_2xx_token_that_also_has_an_error_field():
    transport = FakeTransport(respond(200, {**TOKEN_BODY, "error": "authorization_pending"}))

    token = await poll_once_op(make_client(transport), client_id="public-app")

    assert token.access_token == "at"


async def test_poll_once_rejects_an_empty_device_code():
    transport = FakeTransport()
    credential = FakeCredential()

    with pytest.raises(ConfigError, match=exactly("authorization.device_code is empty")):
        await make_client(transport).poll_device_token(
            authorization().model_copy(update={"device_code": ""}),
            credential=credential,
            metadata=METADATA,
        )

    assert transport.requests == []
    assert credential.calls == []


@pytest.mark.parametrize(
    ("response", "error_type", "status", "code", "retryable"), ERROR_RESPONSES
)
@pytest.mark.parametrize("operation", SINGLE_REQUEST_OPERATIONS)
async def test_error_responses(operation, response, error_type, status, code, retryable):
    transport = FakeTransport(response)

    with pytest.raises(error_type) as exc:
        await operation(make_client(transport), client_id="public-app")

    assert type(exc.value) is error_type
    assert exc.value.status_code == status
    assert getattr(exc.value, "error", None) == code
    assert exc.value.retryable is retryable
    if code == "invalid_response":
        assert exc.value.error_description == "The response body does not decode"
    if code == "invalid_target":
        assert exc.value.error_description == "no resource"
    assert exc.value.operation == OPERATION_NAMES[operation]
    if isinstance(exc.value, OAuthHttpError) and isinstance(response, HttpResponse):
        assert exc.value.response_body == response.body.decode()
    if isinstance(response, BaseException):
        assert exc.value.__cause__ is response


# Poll once


async def test_row_7_poll_once():
    transport = FakeTransport(respond(200, TOKEN_BODY))
    credential = FakeCredential()

    token = await make_client(transport).poll_device_token(
        authorization(), credential=credential, metadata=METADATA
    )

    [req] = transport.requests
    assert (req.method, req.url) == ("POST", TOKEN_ENDPOINT)
    assert req.headers == {**FORM_HEADERS, "X-Client-Auth": "credential-header"}
    assert form(req) == {
        "grant_type": [DEVICE_GRANT],
        "device_code": ["dc"],
        "client_assertion": ["assertion-1"],
        "client_assertion_type": [JWT_BEARER],
    }
    assert credential.calls == [(ISSUER, TOKEN_ENDPOINT)]
    assert transport.timeouts == [7.0]
    assert (token.access_token, token.refresh_token, token.id_token) == (
        "at",
        "rt",
        "header.claims.sig",
    )


@pytest.mark.parametrize("code", ["authorization_pending", "slow_down"])
async def test_poll_once_returns_pending_codes_as_typed_errors(code):
    transport = FakeTransport(respond(400, {"error": code}))

    with pytest.raises(DeviceAuthorizationError) as exc:
        await make_client(transport).poll_device_token(
            authorization(), client_id="public-app", metadata=METADATA
        )

    assert exc.value.error == code
    assert exc.value.status_code == 400
    assert exc.value.retryable is True


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"token_type": "Bearer"}, id="no-access_token"),
        pytest.param({"access_token": ""}, id="empty-access_token"),
        pytest.param({"access_token": 7}, id="access_token-not-a-string"),
    ],
)
async def test_poll_once_malformed_token_response(body):
    transport = FakeTransport(respond(200, body))

    with pytest.raises(DeviceAuthorizationError) as exc:
        await make_client(transport).poll_device_token(
            authorization(), client_id="public-app", metadata=METADATA
        )

    assert exc.value.error == "invalid_response"
    assert exc.value.error_description == "Device token response is missing access_token"
    assert exc.value.status_code == 200
    assert exc.value.retryable is True


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_timeout_argument_bounds_the_request(operation):
    responses = {
        request_op: device_response(),
        poll_once_op: respond(200, TOKEN_BODY),
        poll_until_done_op: respond(200, TOKEN_BODY),
    }
    transport = FakeTransport(responses[operation])

    await operation(make_client(transport), client_id="public-app", timeout=2.5)

    assert transport.timeouts == [2.5]


class SlowCredential(FakeCredential):
    """A FakeCredential that waits ``delay`` seconds, or raises ``error``."""

    def __init__(self, delay: float = 0.0, error: BaseException | None = None):
        super().__init__()
        self.delay = delay
        self.error = error

    async def client_authentication(self, *, issuer: str, endpoint: str) -> ClientAuthentication:
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return await super().client_authentication(issuer=issuer, endpoint=endpoint)


class SlowTransport(FakeTransport):
    """A FakeTransport that records each request, then waits ``delay``
    seconds before it answers."""

    def __init__(self, *responses, delay: float):
        super().__init__(*responses)
        self.delay = delay

    async def request_raw(self, req: HttpRequest, *, timeout: float | None = None) -> HttpResponse:
        self.requests.append(req)
        self.timeouts.append(timeout)
        await asyncio.sleep(self.delay)
        return self.responses.pop(0)


SINGLE_REQUESTS = [
    pytest.param(request_op, device_response(), id="request"),
    pytest.param(poll_once_op, respond(200, TOKEN_BODY), id="poll-once"),
]


# The timeout is 0.2 seconds. In the "together" row each step alone takes
# less, so only a bound on both steps together ends it.
@pytest.mark.parametrize(("operation", "response"), SINGLE_REQUESTS)
@pytest.mark.parametrize(
    ("credential_delay", "transport_delay", "requests_sent"),
    [
        pytest.param(5.0, 0.0, 0, id="credential-hangs"),
        pytest.param(0.0, 5.0, 1, id="request-hangs"),
        pytest.param(0.15, 0.15, 1, id="together"),
    ],
)
async def test_timeout_bounds_the_credential_and_the_request_together(
    operation, response, credential_delay, transport_delay, requests_sent
):
    transport = SlowTransport(response, delay=transport_delay)

    with pytest.raises(NetworkError) as exc:
        await operation(
            make_client(transport), credential=SlowCredential(credential_delay), timeout=0.2
        )

    assert exc.value.operation == OPERATION_NAMES[operation]
    assert isinstance(exc.value.cause, asyncio.TimeoutError)
    assert len(transport.requests) == requests_sent


# A credential's own exception propagates unchanged, even one of the types
# the request itself raises.
@pytest.mark.parametrize(("operation", "response"), SINGLE_REQUESTS)
@pytest.mark.parametrize(
    "error",
    [
        pytest.param(asyncio.TimeoutError("token source timed out"), id="TimeoutError"),
        pytest.param(NetworkError(OSError("token source unreachable")), id="NetworkError"),
    ],
)
async def test_credential_error_propagates_unchanged(operation, response, error):
    transport = FakeTransport(response)

    with pytest.raises(type(error)) as exc:
        await operation(make_client(transport), credential=SlowCredential(error=error))

    assert exc.value is error
    assert exc.value.__cause__ is None
    assert transport.requests == []


async def test_row_22_web_identity_signs_a_new_assertion_per_request(tmp_path):
    credential = WebIdentity(
        server_name="device", storage_dir=str(tmp_path), client_id="registered-client"
    )
    transport = FakeTransport(
        device_response(), respond(400, PENDING), respond(200, TOKEN_BODY)
    )
    client = make_client(transport)

    auth = await client.request_device_authorization(
        credential=credential, metadata=METADATA
    )
    with pytest.raises(DeviceAuthorizationError):
        await client.poll_device_token(auth, credential=credential, metadata=METADATA)
    await client.poll_device_token(auth, credential=credential, metadata=METADATA)

    claims = [jwt_claims(form(r)["client_assertion"][0]) for r in transport.requests]
    assert [c["aud"] for c in claims] == [DEVICE_ENDPOINT, TOKEN_ENDPOINT, TOKEN_ENDPOINT]
    assert {(c["iss"], c["sub"]) for c in claims} == {
        ("registered-client", "registered-client")
    }
    assert len({c["jti"] for c in claims}) == 3


@pytest.mark.parametrize("wait_for_token", [True, False])
async def test_row_22_client_binds_web_identity(tmp_path, wait_for_token):
    credential = WebIdentity(
        server_name="device", storage_dir=str(tmp_path), client_id="registered-client"
    )
    transport = FakeTransport(
        device_response(), respond(400, PENDING), respond(200, TOKEN_BODY)
    )
    client = AsyncClient(
        ISSUER, credential=credential, transport=transport, clock=FakeClock()
    )

    auth = await client.request_device_authorization(metadata=METADATA)
    if wait_for_token:
        await client.wait_for_device_token(auth, metadata=METADATA)
    else:
        with pytest.raises(DeviceAuthorizationError):
            await client.poll_device_token(auth, metadata=METADATA)
        await client.poll_device_token(auth, metadata=METADATA)

    claims = [jwt_claims(form(r)["client_assertion"][0]) for r in transport.requests]
    assert [c["aud"] for c in claims] == [
        DEVICE_ENDPOINT, TOKEN_ENDPOINT, TOKEN_ENDPOINT
    ]
    assert len({c["jti"] for c in claims}) == 3


@pytest.mark.parametrize("wait_for_token", [True, False])
async def test_row_2_client_binds_public_client_id(wait_for_token):
    transport = FakeTransport(
        device_response(), respond(400, PENDING), respond(200, TOKEN_BODY)
    )
    client = AsyncClient(
        ISSUER, client_id="public-app", transport=transport, clock=FakeClock()
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", LegacyGrantCredentialWarning)
        auth = await client.request_device_authorization(metadata=METADATA)
        if wait_for_token:
            await client.wait_for_device_token(auth, metadata=METADATA)
        else:
            with pytest.raises(DeviceAuthorizationError):
                await client.poll_device_token(auth, metadata=METADATA)
            await client.poll_device_token(auth, metadata=METADATA)

    assert [form(r)["client_id"] for r in transport.requests] == [["public-app"]] * 3
    assert all("Authorization" not in r.headers for r in transport.requests)


@pytest.mark.parametrize("name", ["credential", "client_id"])
async def test_row_3_per_call_authentication_warns(name):
    value = FakeCredential() if name == "credential" else "public-app"
    transport = FakeTransport(device_response(), respond(200, TOKEN_BODY))
    client = make_client(transport)

    with pytest.warns(
        LegacyGrantCredentialWarning,
        match=f"Passing {name} to AsyncClient.request_device_authorization",
    ):
        auth = await client.request_device_authorization(
            metadata=METADATA, **{name: value}
        )
    with pytest.warns(
        LegacyGrantCredentialWarning,
        match=f"Passing {name} to AsyncClient.poll_device_token",
    ):
        await client.poll_device_token(auth, metadata=METADATA, **{name: value})


async def test_row_24_multi_resource_token_is_not_associated_with_a_resource():
    body = {**TOKEN_BODY, "resource": "https://a.example.com"}
    transport = FakeTransport(device_response(), respond(200, body))
    client = make_client(transport)

    auth = await client.request_device_authorization(
        client_id="public-app",
        resources=["https://a.example.com", "https://b.example.com"],
        metadata=METADATA,
    )
    token = await client.poll_device_token(auth, client_id="public-app", metadata=METADATA)

    assert token.access_token == "at"
    assert "resource" not in {f.name for f in dataclasses.fields(TokenResponse)}
    assert "resources" not in DeviceAuthorization.model_fields
    assert token.raw == body


# Poll until done


def wait(client: AsyncClient, auth: DeviceAuthorization | None = None, **kwargs):
    kwargs.setdefault("client_id", "public-app")
    kwargs.setdefault("metadata", METADATA)
    return client.wait_for_device_token(auth or authorization(), **kwargs)


async def test_row_8_pending_twice_then_token():
    transport = FakeTransport(
        respond(400, PENDING), respond(400, PENDING), respond(200, TOKEN_BODY)
    )
    clock = FakeClock()

    token = await wait(make_client(transport, clock))

    assert len(transport.requests) == 3
    assert all(form(r) == {
        "grant_type": [DEVICE_GRANT],
        "device_code": ["dc"],
        "client_id": ["public-app"],
    } for r in transport.requests)
    assert transport.timeouts == [7.0, 7.0, 7.0]
    assert clock.sleeps == [5, 5, 5]
    assert (token.access_token, token.refresh_token, token.id_token) == (
        "at",
        "rt",
        "header.claims.sig",
    )


# Each slow_down adds 5 seconds to the interval in force, so the increases
# accumulate (RFC 8628 Section 3.5).
@pytest.mark.parametrize(
    ("responses", "sleeps"),
    [
        pytest.param([SLOW_DOWN, PENDING], [5, 10, 10], id="row-9-slow_down-then-pending"),
        pytest.param([SLOW_DOWN, SLOW_DOWN], [5, 10, 15], id="two-slow_down-in-a-row"),
        pytest.param(
            [SLOW_DOWN, PENDING, SLOW_DOWN, PENDING],
            [5, 10, 10, 15, 15],
            id="slow_down-after-pending",
        ),
    ],
)
async def test_row_9_slow_down_adds_five_seconds_for_every_later_poll(responses, sleeps):
    transport = FakeTransport(
        *[respond(400, body) for body in responses], respond(200, TOKEN_BODY)
    )
    clock = FakeClock()

    await wait(make_client(transport, clock))

    assert clock.sleeps == sleeps
    assert len(transport.requests) == len(sleeps)


@pytest.mark.parametrize(
    ("expires_in", "polls"),
    [
        pytest.param(10, 2, id="row-11-poll-at-expires_at-is-sent"),
        pytest.param(9, 1, id="row-11-poll-after-expires_at-is-not-sent"),
    ],
)
async def test_row_11_expiry_boundary(expires_in, polls):
    transport = FakeTransport(*[respond(400, PENDING)] * polls)
    clock = FakeClock()

    with pytest.raises(DeviceAuthorizationError) as exc:
        await wait(make_client(transport, clock), authorization(expires_in=expires_in))

    assert exc.value.error == "expired_token"
    assert len(transport.requests) == polls
    assert clock.sleeps == [5] * polls


class SuspendingClock(FakeClock):
    """A wait that takes longer than asked, as across a host suspend."""

    async def sleep(self, seconds: float) -> None:
        await super().sleep(seconds)
        self.current += timedelta(seconds=600)


async def test_row_11_expiry_is_checked_again_after_the_wait():
    transport = FakeTransport()
    clock = SuspendingClock()

    with pytest.raises(DeviceAuthorizationError) as exc:
        await wait(make_client(transport, clock))

    assert exc.value.error == "expired_token"
    assert exc.value.error_description == (
        "The device code expired during the wait before the next poll"
    )
    assert clock.sleeps == [5]
    assert transport.requests == []


@pytest.mark.parametrize(
    "code",
    [
        pytest.param("access_denied", id="row-10-access_denied"),
        pytest.param("expired_token", id="row-12-expired_token"),
        pytest.param("invalid_grant", id="row-12-invalid_grant"),
        pytest.param("invalid_client", id="invalid_client"),
    ],
)
async def test_rows_10_and_12_terminal_oauth_error_stops_the_loop(code):
    transport = FakeTransport(respond(400, PENDING), respond(400, {"error": code}))

    with pytest.raises(DeviceAuthorizationError) as exc:
        await wait(make_client(transport))

    assert type(exc.value) is DeviceAuthorizationError
    assert (exc.value.error, exc.value.status_code) == (code, 400)
    assert exc.value.retryable is False
    assert len(transport.requests) == 2


async def test_row_11_clock_passes_expires_at_while_pending():
    transport = FakeTransport(respond(400, PENDING), respond(400, PENDING))
    clock = FakeClock()

    with pytest.raises(DeviceAuthorizationError) as exc:
        await wait(make_client(transport, clock), authorization(expires_in=12))

    assert exc.value.error == "expired_token"
    assert exc.value.error_description == "The device code expires before the next poll"
    assert exc.value.retryable is False
    assert len(transport.requests) == 2
    assert clock.sleeps == [5, 5]


def network_failure() -> NetworkError:
    return NetworkError(
        cause=httpx.ConnectTimeout("timed out"), operation="POST", retriable=False
    )


@pytest.mark.parametrize(
    ("failure", "cause_type", "status", "interval"),
    [
        pytest.param(network_failure(), NetworkError, None, 10, id="row-13-network"),
        pytest.param(respond(503, b"unavailable"), OAuthHttpError, 503, 5, id="row-13-5xx"),
        pytest.param(respond(429, b"slow"), OAuthHttpError, 429, 5, id="row-17-429"),
        pytest.param(
            respond(503, {"error": "temporarily_unavailable"}),
            DeviceAuthorizationError,
            503,
            5,
            id="row-26-503-with-oauth-body",
        ),
        pytest.param(
            respond(503, {"error": "access_denied"}),
            DeviceAuthorizationError,
            503,
            5,
            id="row-29-503-with-permanent-code",
        ),
        pytest.param(
            respond(429, {"error": "invalid_grant"}),
            DeviceAuthorizationError,
            429,
            5,
            id="429-with-invalid_grant",
        ),
        pytest.param(undecodable(503), OAuthHttpError, 503, 5, id="row-32-503-not-gzip"),
    ],
)
async def test_rows_13_17_26_29_32_failure_stops_with_interval(
    failure, cause_type, status, interval
):
    transport = FakeTransport(respond(400, PENDING), failure)
    clock = FakeClock()

    with pytest.raises(DevicePollError) as exc:
        await wait(make_client(transport, clock))

    assert len(transport.requests) == 2
    assert clock.sleeps == [5, 5]
    assert exc.value.retryable is True
    assert exc.value.interval == interval
    cause = exc.value.__cause__
    assert type(cause) is cause_type
    assert exc.value.cause is cause
    assert cause.retryable is True
    assert getattr(cause, "status_code", None) == status
    if isinstance(failure, NetworkError) and not isinstance(failure, ResponseBodyDecodeError):
        assert cause is failure
    if isinstance(cause, DeviceAuthorizationError):
        assert cause.error == json.loads(failure.body)["error"]


async def test_slow_down_then_5xx_reports_the_raised_interval_not_doubled():
    transport = FakeTransport(respond(400, SLOW_DOWN), respond(503, b"unavailable"))

    with pytest.raises(DevicePollError) as exc:
        await wait(make_client(transport))

    assert exc.value.interval == 5 + 5


async def test_row_13_network_error_then_5xx_on_a_second_call():
    transport = FakeTransport(network_failure(), respond(503, b"unavailable"))
    client = make_client(transport)

    with pytest.raises(DevicePollError) as first:
        await wait(client)
    with pytest.raises(DevicePollError) as second:
        await wait(client)

    assert (first.value.interval, second.value.interval) == (10, 5)
    assert isinstance(first.value.cause, NetworkError)
    assert isinstance(second.value.cause, OAuthHttpError)
    assert len(transport.requests) == 2


@pytest.mark.parametrize(
    ("response", "error_type", "status", "code"),
    [
        pytest.param(
            respond(404, b"not found"), OAuthHttpError, 404, None, id="4xx-without-oauth-body"
        ),
        pytest.param(
            undecodable(200),
            DeviceAuthorizationError,
            200,
            "invalid_response",
            id="row-31-200-not-gzip",
        ),
        pytest.param(
            respond(200, b"<html>"),
            DeviceAuthorizationError,
            200,
            "invalid_response",
            id="200-not-json",
        ),
    ],
)
async def test_row_31_other_failures_return_as_is(response, error_type, status, code):
    transport = FakeTransport(response)

    with pytest.raises(error_type) as exc:
        await wait(make_client(transport))

    assert type(exc.value) is error_type
    assert exc.value.status_code == status
    assert getattr(exc.value, "error", None) == code
    assert len(transport.requests) == 1


async def test_row_14_cancelled_during_wait():
    block = asyncio.Event()
    transport = FakeTransport(respond(200, TOKEN_BODY))
    clock = FakeClock(block=block)
    task = asyncio.create_task(wait(make_client(transport, clock)))
    while not clock.sleeps:
        await asyncio.sleep(0)

    task.cancel()
    block.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.requests == []


async def test_row_15_persisted_authorization():
    original = authorization()
    original.device_code = "persisted-device-code"
    restored = DeviceAuthorization.model_validate_json(original.model_dump_json())
    transport = FakeTransport(respond(200, TOKEN_BODY))

    await wait(make_client(transport), restored)

    assert restored == original
    assert form(transport.requests[0])["device_code"] == ["persisted-device-code"]


async def test_row_16_slow_down_then_network_error():
    failure = network_failure()
    transport = FakeTransport(respond(400, SLOW_DOWN), failure)

    with pytest.raises(DevicePollError) as exc:
        await wait(make_client(transport))

    assert exc.value.retryable is True
    assert exc.value.interval == 2 * (5 + 5)
    assert exc.value.__cause__ is failure


async def test_row_18_resume_with_the_reported_interval():
    transport = FakeTransport(respond(400, SLOW_DOWN), network_failure())
    clock = FakeClock()
    client = make_client(transport, clock)
    with pytest.raises(DevicePollError) as exc:
        await wait(client)
    transport.responses.append(respond(200, TOKEN_BODY))
    clock.sleeps.clear()

    await wait(client, interval=exc.value.interval)

    assert clock.sleeps == [20]


@pytest.mark.parametrize(
    ("auth", "interval", "message"),
    [
        pytest.param(
            authorization(),
            3,
            "interval 3 is below the device authorization interval 5",
            id="row-19",
        ),
        pytest.param(authorization(), 7.5, "interval 7.5 is not an integer", id="float"),
        pytest.param(authorization(), "10", "interval '10' is not an integer", id="string"),
        pytest.param(
            authorization(interval=0),
            None,
            "authorization.interval 0 is not positive",
            id="persisted-interval-zero",
        ),
        pytest.param(
            authorization(interval=-5),
            None,
            "authorization.interval -5 is not positive",
            id="persisted-interval-negative",
        ),
    ],
)
async def test_row_19_interval_misconfigured(auth, interval, message):
    transport = FakeTransport()
    clock = FakeClock()

    with pytest.raises(ConfigError, match=exactly(message)):
        await wait(make_client(transport, clock), auth, interval=interval)

    assert transport.requests == []
    assert clock.sleeps == []


async def test_row_19_interval_equal_to_the_authorization_interval():
    transport = FakeTransport(respond(200, TOKEN_BODY))
    clock = FakeClock()

    await wait(make_client(transport, clock), interval=5)

    assert clock.sleeps == [5]


async def test_empty_device_code_is_a_config_error():
    transport = FakeTransport()
    auth = authorization()
    auth.device_code = ""

    with pytest.raises(ConfigError, match=exactly("authorization.device_code is empty")):
        await wait(make_client(transport), auth)

    assert transport.requests == []


async def test_row_20_cancelled_while_a_poll_is_in_flight():
    block = asyncio.Event()
    transport = FakeTransport(respond(400, PENDING), respond(200, TOKEN_BODY), block=block)
    credential = FakeCredential()
    task = asyncio.create_task(
        wait(make_client(transport), client_id=None, credential=credential)
    )
    while not transport.requests:
        await asyncio.sleep(0)

    task.cancel()
    block.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(transport.requests) == 1
    assert len(credential.calls) == 1



class BlockingCredential(FakeCredential):
    """A FakeCredential that waits for ``block`` before it answers."""

    def __init__(self, block: asyncio.Event):
        super().__init__()
        self.block = block
        self.waiting = False

    async def client_authentication(self, *, issuer: str, endpoint: str) -> ClientAuthentication:
        self.waiting = True
        await self.block.wait()
        return await super().client_authentication(issuer=issuer, endpoint=endpoint)


# The bounded call finishes in the same event loop step as the cancellation.
# asyncio.wait_for on Python 3.10 and 3.11 returns the result then, and the
# loop polls again (CPython gh-86296). The cancellation must win.
@pytest.mark.parametrize("blocked", ["credential", "request"])
async def test_row_20_cancelled_as_the_bounded_call_finishes(blocked):
    block = asyncio.Event()
    credential = BlockingCredential(block) if blocked == "credential" else FakeCredential()
    transport = FakeTransport(
        respond(400, PENDING),
        respond(200, TOKEN_BODY),
        block=block if blocked == "request" else None,
    )
    task = asyncio.create_task(
        wait(make_client(transport), client_id=None, credential=credential, timeout=30)
    )
    while not (transport.requests or getattr(credential, "waiting", False)):
        await asyncio.sleep(0)

    block.set()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    # On Python 3.11 and later the cancellation can end the blocked step
    # before it returns. On 3.10 the step finishes first. Either way the
    # loop never polls a second time.
    assert len(credential.calls) <= 1
    assert len(transport.requests) <= 1


async def test_row_21_interval_longer_than_time_left():
    transport = FakeTransport()
    clock = FakeClock()

    with pytest.raises(DeviceAuthorizationError) as exc:
        await wait(make_client(transport, clock), authorization(expires_in=10), interval=15)

    assert type(exc.value) is DeviceAuthorizationError
    assert (exc.value.error, exc.value.status_code) == ("expired_token", None)
    assert exc.value.error_description == "The device code expires before the next poll"
    assert exc.value.retryable is False
    assert transport.requests == []
    assert clock.sleeps == []


async def test_row_22_one_flow_with_poll_until_done(tmp_path):
    credential = WebIdentity(
        server_name="device", storage_dir=str(tmp_path), client_id="registered-client"
    )
    transport = FakeTransport(
        device_response(), respond(400, PENDING), respond(200, TOKEN_BODY)
    )
    client = make_client(transport)

    auth = await client.request_device_authorization(
        credential=credential, metadata=METADATA
    )
    await client.wait_for_device_token(auth, credential=credential, metadata=METADATA)

    claims = [jwt_claims(form(r)["client_assertion"][0]) for r in transport.requests]
    assert [c["aud"] for c in claims] == [DEVICE_ENDPOINT, TOKEN_ENDPOINT, TOKEN_ENDPOINT]
    assert len({c["jti"] for c in claims}) == 3


class FailingCredential(FakeCredential):
    """Authenticates the first poll, then raises ``error`` on the next one."""

    def __init__(self, error: Exception):
        super().__init__()
        self.error = error

    async def client_authentication(
        self, *, issuer: str, endpoint: str
    ) -> ClientAuthentication:
        if self.calls:
            raise self.error
        return await super().client_authentication(issuer=issuer, endpoint=endpoint)


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(
            WorkloadIdentityRuntimeError("token source failed", source="custom"),
            id="row-27-source-error",
        ),
        pytest.param(ConfigError("no zone"), id="row-27-config-error"),
        pytest.param(
            NetworkError(cause=httpx.ConnectError("refused"), retriable=False),
            id="row-27-network-error-from-the-credential",
        ),
        pytest.param(
            asyncio.TimeoutError("token source timed out"),
            id="row-27-timeout-error-from-the-credential",
        ),
    ],
)
async def test_row_27_credential_failure_returns_as_is(error):
    transport = FakeTransport(respond(400, PENDING))
    credential = FailingCredential(error)

    with pytest.raises(type(error)) as exc:
        await wait(make_client(transport), client_id=None, credential=credential)

    assert exc.value is error
    assert len(transport.requests) == 1


async def test_credential_that_outlasts_the_timeout_is_a_network_error():
    transport = FakeTransport()

    with pytest.raises(DevicePollError) as exc:
        await wait(
            make_client(transport), client_id=None, credential=SlowCredential(5.0), timeout=0.05
        )

    assert exc.value.interval == 2 * 5
    assert type(exc.value.__cause__) is NetworkError
    assert isinstance(exc.value.__cause__.cause, asyncio.TimeoutError)
    assert transport.requests == []


@pytest.mark.parametrize(
    ("deadline", "expected"),
    [
        pytest.param(30.0, DevicePollError, id="row-28-longer-deadline"),
        # A caller deadline shorter than the request timeout ends the loop
        # with the caller's own TimeoutError, not a DevicePollError.
        pytest.param(0.05, asyncio.TimeoutError, id="shorter-deadline"),
    ],
)
async def test_row_28_request_timeout_holds_under_a_longer_caller_deadline(deadline, expected):
    # A local server that accepts each connection and never answers. The
    # client's real httpx transport has a 0.2 second request timeout, so the
    # poll ends at 0.2 seconds whatever the caller's deadline.
    writers: list[asyncio.StreamWriter] = []

    async def never_respond(reader, writer):
        writers.append(writer)
        await reader.read(65536)

    server = await asyncio.start_server(never_respond, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    token_endpoint = f"http://127.0.0.1:{port}/oauth/token"
    client = AsyncClient(
        ISSUER,
        clock=FakeClock(),
        config=ClientConfig(
            enable_metadata_discovery=False, auto_register_client=False, timeout=0.2
        ),
    )

    try:
        with pytest.raises(expected) as exc:
            await asyncio.wait_for(
                wait(client, metadata=metadata(token_endpoint=token_endpoint)), deadline
            )
    finally:
        await client.aclose()
        for writer in writers:
            writer.close()
        server.close()
        await server.wait_closed()

    if expected is DevicePollError:
        assert exc.value.interval == 2 * 5
        assert exc.value.retryable is True
        assert type(exc.value.__cause__) is NetworkError
        # asyncio.wait_for and httpx's read timeout both use 0.2 seconds, so
        # either one ends the poll.
        assert isinstance(
            exc.value.__cause__.cause, (httpx.ReadTimeout, asyncio.TimeoutError)
        )


class CancelAtWaitEnd(FakeClock):
    """Cancels the polling task as the wait ends, then returns normally."""

    async def sleep(self, seconds: float) -> None:
        await super().sleep(seconds)
        task = asyncio.current_task()
        assert task is not None
        task.cancel()


async def test_row_33_cancelled_when_the_wait_ends():
    transport = FakeTransport(respond(200, TOKEN_BODY))
    clock = CancelAtWaitEnd()
    credential = FakeCredential()

    task = asyncio.create_task(
        wait(make_client(transport, clock), client_id=None, credential=credential)
    )

    with pytest.raises(asyncio.CancelledError):
        await task

    assert clock.sleeps == [5]
    assert credential.calls == []
    assert transport.requests == []


# Row 35: the client's issuer selects the zone when metadata is supplied.


def success_response(operation) -> HttpResponse:
    if operation is request_op:
        return device_response()
    return respond(200, TOKEN_BODY)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    ("client_issuer", "metadata_issuer"),
    [
        pytest.param(f"{ISSUER}/", ISSUER, id="slash-on-client-issuer"),
        pytest.param(ISSUER, f"{ISSUER}/", id="slash-on-metadata-issuer"),
    ],
)
async def test_row_35_matching_metadata_issuer_authenticates_with_client_issuer(
    operation, client_issuer, metadata_issuer
):
    transport = FakeTransport(success_response(operation))
    client = AsyncClient(
        client_issuer,
        transport=transport,
        clock=FakeClock(),
        config=ClientConfig(enable_metadata_discovery=False, auto_register_client=False),
    )
    credential = FakeCredential()

    await operation(
        client,
        metadata=dataclasses.replace(METADATA, issuer=metadata_issuer),
        credential=credential,
    )

    endpoint = DEVICE_ENDPOINT if operation is request_op else TOKEN_ENDPOINT
    assert credential.calls == [(client.issuer, endpoint)]
    assert client.issuer == ISSUER
    assert [req.url for req in transport.requests] == [endpoint]


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_row_35_metadata_for_another_zone_is_a_configuration_error(operation):
    transport = FakeTransport()
    credential = FakeCredential()

    with pytest.raises(
        ConfigError,
        match=exactly(
            f"issuer {ISSUER} does not match the metadata issuer "
            "https://other.example.com"
        ),
    ):
        await operation(
            make_client(transport),
            metadata=dataclasses.replace(METADATA, issuer="https://other.example.com"),
            credential=credential,
        )

    assert credential.calls == []
    assert transport.requests == []


@pytest.mark.parametrize("status,retryable", [(200, True), (400, False), (429, True), (503, True)])
async def test_row_36_poll_once_oversized_body(status, retryable):
    transport = FakeTransport(
        ResponseBodyTooLargeError(status_code=status, operation="POST /token")
    )
    client = make_client(transport)

    with pytest.raises(DeviceAuthorizationError) as exc:
        await client.poll_device_token(
            authorization(), client_id="client-1", metadata=METADATA
        )

    assert exc.value.error == "invalid_response"
    assert exc.value.status_code == status
    assert exc.value.retryable is retryable


@pytest.mark.parametrize("status,retryable", [(200, True), (400, False)])
async def test_row_36_poll_until_done_oversized_body_direct(status, retryable):
    transport = FakeTransport(
        respond(400, SLOW_DOWN),
        ResponseBodyTooLargeError(status_code=status, operation="POST /token"),
    )
    clock = FakeClock()

    with pytest.raises(DeviceAuthorizationError) as exc:
        await wait(make_client(transport, clock))

    assert exc.value.error == "invalid_response"
    assert exc.value.status_code == status
    assert exc.value.retryable is retryable
    assert not isinstance(exc.value, DevicePollError)


@pytest.mark.parametrize("status", [429, 503])
async def test_row_36_poll_until_done_oversized_body_transient_polling_error(status):
    transport = FakeTransport(
        respond(400, SLOW_DOWN),
        ResponseBodyTooLargeError(status_code=status, operation="POST /token"),
    )
    clock = FakeClock()

    with pytest.raises(DevicePollError) as exc:
        await wait(make_client(transport, clock))

    assert exc.value.retryable is True
    assert exc.value.interval == 10
    assert isinstance(exc.value.cause, DeviceAuthorizationError)
    assert exc.value.cause.error == "invalid_response"
    assert exc.value.cause.status_code == status
    assert exc.value.cause.retryable is True
