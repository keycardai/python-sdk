"""Device authorization grant (RFC 8628) on AsyncClient.

Test names start with the row of the unit test table in the Keycard SDK spec
(specs/oauth-client/device-authorization.md). Every test runs against a fake
transport and a fake clock, so no test sends a real request or waits in real
time. The fake clock records each wait and advances its time by it.
"""

import asyncio
import base64
import dataclasses
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from keycardai.oauth import (
    AsyncClient,
    AuthorizationServerMetadata,
    ClientAuthentication,
    ClientConfig,
    ConfigError,
    DeviceAuthorization,
    DeviceAuthorizationError,
    DevicePollError,
    NetworkError,
    OAuthHttpError,
    TokenResponse,
)
from keycardai.oauth.http._wire import HttpRequest, HttpResponse
from keycardai.oauth.server.credentials import WebIdentity
from keycardai.oauth.server.exceptions import WorkloadIdentityRuntimeError

ISSUER = "https://zone.example.com"
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
    assert req.headers["X-Client-Auth"] == "credential-header"
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
    assert "Authorization" not in req.headers


BOTH = {"credential": FakeCredential(), "client_id": "public-app"}
NEITHER: dict = {}
NO_CAPABILITY = {"credential": ExchangeOnlyCredential()}


def _operations():
    return [
        pytest.param(
            lambda c, auth: c.request_device_authorization(metadata=METADATA, **auth),
            id="request",
        ),
        pytest.param(
            lambda c, auth: c.poll_device_token(authorization(), metadata=METADATA, **auth),
            id="poll-once",
        ),
        pytest.param(
            lambda c, auth: c.wait_for_device_token(
                authorization(), metadata=METADATA, **auth
            ),
            id="poll-until-done",
        ),
    ]


@pytest.mark.parametrize(
    ("auth", "message"),
    [
        pytest.param(BOTH, "exactly one", id="row-3-both"),
        pytest.param(NEITHER, "exactly one", id="row-3-neither"),
        pytest.param(
            NO_CAPABILITY,
            "ExchangeOnlyCredential does not provide client authentication",
            id="row-23-no-client-authentication",
        ),
    ],
)
@pytest.mark.parametrize("operation", _operations())
async def test_rows_3_and_23_client_authentication_misconfigured(operation, auth, message):
    transport = FakeTransport()
    clock = FakeClock()

    with pytest.raises(ConfigError, match=message):
        await operation(make_client(transport, clock), auth)

    assert transport.requests == []
    assert clock.sleeps == []


async def test_row_4_metadata_without_device_endpoint():
    transport = FakeTransport()
    metadata = AuthorizationServerMetadata(issuer=ISSUER, token_endpoint=TOKEN_ENDPOINT)

    with pytest.raises(ConfigError, match="does not advertise a device_authorization_endpoint"):
        await make_client(transport).request_device_authorization(
            client_id="public-app", metadata=metadata
        )

    assert transport.requests == []


async def test_row_4_discovered_metadata_without_device_endpoint():
    transport = FakeTransport(
        respond(200, {"issuer": ISSUER, "token_endpoint": TOKEN_ENDPOINT})
    )
    client = AsyncClient(ISSUER, transport=transport, clock=FakeClock())

    with pytest.raises(ConfigError, match="does not advertise a device_authorization_endpoint"):
        await client.request_device_authorization(client_id="public-app")

    assert [r.method for r in transport.requests] == ["GET"]


async def test_discovered_device_endpoint_is_used():
    transport = FakeTransport(
        respond(
            200,
            {
                "issuer": ISSUER,
                "token_endpoint": TOKEN_ENDPOINT,
                "device_authorization_endpoint": DEVICE_ENDPOINT,
            },
        ),
        device_response(),
    )
    client = AsyncClient(ISSUER, transport=transport, clock=FakeClock())

    await client.request_device_authorization(client_id="public-app")

    assert [(r.method, r.url) for r in transport.requests][1] == ("POST", DEVICE_ENDPOINT)


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


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        pytest.param({"user_code": None}, "user_code", id="row-6-user_code"),
        pytest.param({"device_code": None}, "device_code", id="device_code"),
        pytest.param({"verification_uri": None}, "verification_uri", id="verification_uri"),
        pytest.param({"expires_in": None}, "expires_in", id="expires_in"),
        pytest.param({"expires_in": 0}, "expires_in", id="expires_in-zero"),
    ],
)
async def test_row_6_response_missing_a_required_field(overrides, missing):
    transport = FakeTransport(device_response(**overrides))

    with pytest.raises(DeviceAuthorizationError) as exc:
        await make_client(transport).request_device_authorization(
            client_id="public-app", metadata=METADATA
        )

    assert exc.value.error == "invalid_response"
    assert exc.value.error_description == (
        f"Device authorization response is missing {missing}"
    )


@pytest.mark.parametrize(
    ("response", "error_type", "code"),
    [
        pytest.param(
            respond(400, {"error": "invalid_target"}),
            DeviceAuthorizationError,
            "invalid_target",
            id="oauth-error",
        ),
        pytest.param(respond(502, b"bad gateway"), OAuthHttpError, None, id="http-error"),
    ],
)
async def test_device_authorization_error_responses(response, error_type, code):
    transport = FakeTransport(response)

    with pytest.raises(error_type) as exc:
        await make_client(transport).request_device_authorization(
            client_id="public-app", metadata=METADATA
        )

    assert getattr(exc.value, "error", None) == code


# Poll once


async def test_row_7_poll_once():
    transport = FakeTransport(respond(200, TOKEN_BODY))
    credential = FakeCredential()

    token = await make_client(transport).poll_device_token(
        authorization(), credential=credential, metadata=METADATA
    )

    [req] = transport.requests
    assert (req.method, req.url) == ("POST", TOKEN_ENDPOINT)
    assert req.headers["X-Client-Auth"] == "credential-header"
    assert form(req) == {
        "grant_type": [DEVICE_GRANT],
        "device_code": ["dc"],
        "client_assertion": ["assertion-1"],
        "client_assertion_type": [JWT_BEARER],
    }
    assert credential.calls == [(ISSUER, TOKEN_ENDPOINT)]
    assert token.access_token == "at"


@pytest.mark.parametrize("code", ["authorization_pending", "slow_down"])
async def test_poll_once_returns_pending_codes_as_typed_errors(code):
    transport = FakeTransport(respond(400, {"error": code}))

    with pytest.raises(DeviceAuthorizationError) as exc:
        await make_client(transport).poll_device_token(
            authorization(), client_id="public-app", metadata=METADATA
        )

    assert exc.value.error == code
    assert exc.value.retryable is True


# Poll until done


async def test_row_8_pending_twice_then_token():
    transport = FakeTransport(
        respond(400, PENDING), respond(400, PENDING), respond(200, TOKEN_BODY)
    )
    clock = FakeClock()

    token = await make_client(transport, clock).wait_for_device_token(
        authorization(), client_id="public-app", metadata=METADATA
    )

    assert len(transport.requests) == 3
    assert clock.sleeps == [5, 5, 5]
    assert (token.access_token, token.refresh_token, token.id_token) == (
        "at",
        "rt",
        "header.claims.sig",
    )


async def test_row_9_slow_down_adds_five_seconds_for_every_later_poll():
    transport = FakeTransport(
        respond(400, SLOW_DOWN), respond(400, PENDING), respond(200, TOKEN_BODY)
    )
    clock = FakeClock()

    await make_client(transport, clock).wait_for_device_token(
        authorization(), client_id="public-app", metadata=METADATA
    )

    assert clock.sleeps == [5, 10, 10]


@pytest.mark.parametrize(
    "code",
    [
        pytest.param("access_denied", id="row-10-access_denied"),
        pytest.param("expired_token", id="row-12-expired_token"),
        pytest.param("invalid_grant", id="row-12-invalid_grant"),
    ],
)
async def test_rows_10_and_12_terminal_oauth_error_stops_the_loop(code):
    transport = FakeTransport(respond(400, PENDING), respond(400, {"error": code}))

    with pytest.raises(DeviceAuthorizationError) as exc:
        await make_client(transport).wait_for_device_token(
            authorization(), client_id="public-app", metadata=METADATA
        )

    assert exc.value.error == code
    assert exc.value.retryable is False
    assert len(transport.requests) == 2


async def test_row_11_clock_passes_expires_at_while_pending():
    transport = FakeTransport(respond(400, PENDING), respond(400, PENDING))
    clock = FakeClock()

    with pytest.raises(DeviceAuthorizationError) as exc:
        await make_client(transport, clock).wait_for_device_token(
            authorization(expires_in=12), client_id="public-app", metadata=METADATA
        )

    assert exc.value.error == "expired_token"
    assert exc.value.retryable is False
    assert len(transport.requests) == 2
    assert clock.sleeps == [5, 5]


NETWORK_FAILURE = NetworkError(
    cause=httpx.ConnectTimeout("timed out"), operation="POST", retriable=False
)


@pytest.mark.parametrize(
    ("failure", "cause_type", "status"),
    [
        pytest.param(NETWORK_FAILURE, NetworkError, None, id="row-13-network"),
        pytest.param(respond(503, b"unavailable"), OAuthHttpError, 503, id="row-13-5xx"),
        pytest.param(
            respond(503, {"error": "temporarily_unavailable"}),
            DeviceAuthorizationError,
            None,
            id="row-26-5xx-with-oauth-body",
        ),
        pytest.param(respond(429, b"slow"), OAuthHttpError, 429, id="row-17-429"),
    ],
)
async def test_rows_13_and_17_transport_failure_stops_with_interval(
    failure, cause_type, status
):
    transport = FakeTransport(respond(400, PENDING), failure)
    clock = FakeClock()

    with pytest.raises(DevicePollError) as exc:
        await make_client(transport, clock).wait_for_device_token(
            authorization(), client_id="public-app", metadata=METADATA
        )

    assert len(transport.requests) == 2
    assert clock.sleeps == [5, 5]
    assert exc.value.retryable is True
    assert exc.value.interval == 5
    assert isinstance(exc.value.__cause__, cause_type)
    assert exc.value.cause is exc.value.__cause__
    assert getattr(exc.value.__cause__, "status_code", None) == status
    if isinstance(failure, BaseException):
        assert exc.value.__cause__ is failure


async def test_4xx_without_oauth_body_is_not_wrapped():
    transport = FakeTransport(respond(404, b"not found"))

    with pytest.raises(OAuthHttpError) as exc:
        await make_client(transport).wait_for_device_token(
            authorization(), client_id="public-app", metadata=METADATA
        )

    assert exc.value.status_code == 404
    assert exc.value.retryable is False


async def test_row_14_cancelled_during_wait():
    block = asyncio.Event()
    transport = FakeTransport(respond(200, TOKEN_BODY))
    clock = FakeClock(block=block)
    task = asyncio.create_task(
        make_client(transport, clock).wait_for_device_token(
            authorization(), client_id="public-app", metadata=METADATA
        )
    )
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

    await make_client(transport).wait_for_device_token(
        restored, client_id="public-app", metadata=METADATA
    )

    assert restored == original
    assert form(transport.requests[0])["device_code"] == ["persisted-device-code"]


async def test_row_16_slow_down_then_network_error():
    transport = FakeTransport(respond(400, SLOW_DOWN), NETWORK_FAILURE)

    with pytest.raises(DevicePollError) as exc:
        await make_client(transport).wait_for_device_token(
            authorization(), client_id="public-app", metadata=METADATA
        )

    assert exc.value.retryable is True
    assert exc.value.interval == 10
    assert exc.value.__cause__ is NETWORK_FAILURE


async def test_row_18_resume_with_the_reported_interval():
    transport = FakeTransport(respond(200, TOKEN_BODY))
    clock = FakeClock()

    await make_client(transport, clock).wait_for_device_token(
        authorization(), interval=10, client_id="public-app", metadata=METADATA
    )

    assert clock.sleeps == [10]


async def test_row_19_interval_below_authorization_interval():
    transport = FakeTransport()
    clock = FakeClock()

    with pytest.raises(ConfigError, match="interval 3 is below the device authorization interval 5"):
        await make_client(transport, clock).wait_for_device_token(
            authorization(), interval=3, client_id="public-app", metadata=METADATA
        )

    assert transport.requests == []
    assert clock.sleeps == []


async def test_row_20_cancelled_while_a_poll_is_in_flight():
    block = asyncio.Event()
    transport = FakeTransport(respond(400, PENDING), respond(200, TOKEN_BODY), block=block)
    task = asyncio.create_task(
        make_client(transport).wait_for_device_token(
            authorization(), client_id="public-app", metadata=METADATA
        )
    )
    while not transport.requests:
        await asyncio.sleep(0)

    task.cancel()
    block.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(transport.requests) == 1


async def test_row_21_interval_longer_than_time_left():
    transport = FakeTransport()
    clock = FakeClock()

    with pytest.raises(DeviceAuthorizationError) as exc:
        await make_client(transport, clock).wait_for_device_token(
            authorization(expires_in=10),
            interval=15,
            client_id="public-app",
            metadata=METADATA,
        )

    assert exc.value.error == "expired_token"
    assert transport.requests == []
    assert clock.sleeps == []


async def test_row_22_web_identity_signs_a_new_assertion_per_endpoint(tmp_path):
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
    assert {(c["iss"], c["sub"]) for c in claims} == {
        ("registered-client", "registered-client")
    }
    assert len({c["jti"] for c in claims}) == 3


async def test_row_24_multi_resource_token_is_not_associated_with_a_resource():
    body = {**TOKEN_BODY, "resource": "https://a.example.com"}
    transport = FakeTransport(device_response(), respond(200, body))
    client = make_client(transport)

    auth = await client.request_device_authorization(
        client_id="public-app",
        resources=["https://a.example.com", "https://b.example.com"],
        metadata=METADATA,
    )
    token = await client.wait_for_device_token(
        auth, client_id="public-app", metadata=METADATA
    )

    assert token.access_token == "at"
    assert "resource" not in {f.name for f in dataclasses.fields(TokenResponse)}
    assert "resources" not in DeviceAuthorization.model_fields
    assert token.raw == body


@pytest.mark.parametrize(
    "interval",
    [
        pytest.param(0, id="row-25-zero"),
        pytest.param(-1, id="row-25-negative"),
        pytest.param("5", id="row-25-string"),
        pytest.param(1.5, id="fraction"),
        pytest.param(True, id="boolean"),
    ],
)
async def test_row_25_interval_that_is_not_a_positive_integer(interval):
    transport = FakeTransport(device_response(interval=interval))

    with pytest.raises(DeviceAuthorizationError) as exc:
        await make_client(transport).request_device_authorization(
            client_id="public-app", metadata=METADATA
        )

    assert exc.value.error == "invalid_response"
    assert exc.value.error_description == (
        "Device authorization response has an invalid interval"
    )


@pytest.mark.parametrize(
    ("status", "code"),
    [
        pytest.param(503, "temporarily_unavailable", id="row-26-503"),
        pytest.param(429, "slow_down", id="429-with-oauth-body"),
    ],
)
async def test_row_26_status_wins_over_an_oauth_error_body(status, code):
    transport = FakeTransport(respond(400, SLOW_DOWN), respond(status, {"error": code}))
    clock = FakeClock()

    with pytest.raises(DevicePollError) as exc:
        await make_client(transport, clock).wait_for_device_token(
            authorization(), client_id="public-app", metadata=METADATA
        )

    assert len(transport.requests) == 2
    assert exc.value.retryable is True
    assert exc.value.interval == 10
    assert isinstance(exc.value.__cause__, DeviceAuthorizationError)
    assert exc.value.__cause__.error == code


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
    ],
)
async def test_row_27_credential_failure_returns_as_is(error):
    transport = FakeTransport(respond(400, PENDING))
    credential = FailingCredential(error)

    with pytest.raises(type(error)) as exc:
        await make_client(transport).wait_for_device_token(
            authorization(), credential=credential, metadata=METADATA
        )

    assert exc.value is error
    assert not hasattr(exc.value, "interval")
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "deadline",
    [pytest.param(2.0, id="row-28-short-deadline"), pytest.param(30.0, id="row-28-long-deadline")],
)
async def test_row_28_request_timeout_holds_under_a_longer_caller_deadline(deadline):
    # A local server that accepts each connection and never answers. The
    # client's real httpx transport has a 0.2 second request timeout.
    writers: list[asyncio.StreamWriter] = []

    async def never_respond(reader, writer):
        writers.append(writer)
        await reader.read(65536)

    server = await asyncio.start_server(never_respond, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    token_endpoint = f"http://127.0.0.1:{port}/oauth/token"
    metadata = AuthorizationServerMetadata(issuer=ISSUER, token_endpoint=token_endpoint)
    client = AsyncClient(
        ISSUER,
        clock=FakeClock(),
        config=ClientConfig(
            enable_metadata_discovery=False, auto_register_client=False, timeout=0.2
        ),
    )

    try:
        with pytest.raises(DevicePollError) as exc:
            async with asyncio.timeout(deadline):
                await client.wait_for_device_token(
                    authorization(), client_id="public-app", metadata=metadata
                )
    finally:
        await client.aclose()
        for writer in writers:
            writer.close()
        server.close()
        await server.wait_closed()

    assert exc.value.interval == 5
    assert exc.value.retryable is True
    assert isinstance(exc.value.__cause__, NetworkError)
    assert isinstance(exc.value.__cause__.cause, httpx.TimeoutException)
