"""Refresh with a credential (spec-version 5 of Authorization Code with PKCE).

Covers row 16 of the spec's unit test table on both hosts,
AsyncClient.refresh_token_grant and pkce.refresh_authorization, and on each
entry mode of refresh_authorization. The credential's client
authentication replaces the client's auth strategy.
"""

import base64
import json
import re
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs

import httpx
import pytest

from keycardai.oauth import (
    AsyncClient,
    AuthorizationServerMetadata,
    BasicAuth,
    ClientAuthentication,
    ConfigError,
)
from keycardai.oauth.http._wire import HttpRequest, HttpResponse
from keycardai.oauth.pkce import refresh_authorization
from keycardai.oauth.server.credentials import ClientSecret

ISSUER = "https://zone.example.com"
TOKEN_ENDPOINT = "https://zone.example.com/oauth/token"
RESOURCE_METADATA_URL = "https://api.example.com/.well-known/oauth-protected-resource"
CHALLENGE = f'Bearer resource_metadata="{RESOURCE_METADATA_URL}"'
JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
METADATA = AuthorizationServerMetadata(issuer=ISSUER, token_endpoint=TOKEN_ENDPOINT)

pytestmark = pytest.mark.asyncio


class FakeTransport:
    """Answers discovery with the zone's metadata and every POST with a token."""

    def __init__(self):
        self.requests: list[HttpRequest] = []

    async def request_raw(self, req: HttpRequest, *, timeout: float | None = None):
        self.requests.append(req)
        if req.method == "GET":
            body = {"issuer": ISSUER, "token_endpoint": TOKEN_ENDPOINT}
        else:
            body = {"access_token": "at", "token_type": "Bearer", "refresh_token": "rt2"}
        return HttpResponse(status=200, headers={}, body=json.dumps(body).encode())


class FakeCredential:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def client_authentication(
        self, *, issuer: str, endpoint: str
    ) -> ClientAuthentication:
        self.calls.append((issuer, endpoint))
        return ClientAuthentication(
            headers={"X-Client-Auth": "credential-header"},
            form_fields={"client_assertion": "assertion", "client_assertion_type": JWT_BEARER},
        )


class ExchangeOnlyCredential:
    async def prepare_token_exchange_request(self, client, subject_token, resource, auth_info=None):
        raise AssertionError("not used")


def resource_metadata_client(fetches: list[str]) -> httpx.AsyncClient:
    """An httpx client that serves the protected resource metadata."""

    def handler(request: httpx.Request) -> httpx.Response:
        fetches.append(str(request.url))
        return httpx.Response(200, json={"authorization_servers": [f"{ISSUER}/"]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


Refresh = Callable[..., Awaitable]


@pytest.fixture
def hosts(monkeypatch):
    """Each host and entry mode, all sending through one FakeTransport.

    Returns the transport, the resource metadata fetches, and a refresh call
    per host. Every call takes the same keyword arguments. Discovery is on,
    and the issuer has a trailing slash where the caller passes one.
    """
    transport = FakeTransport()
    fetches: list[str] = []
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        lambda **kwargs: AsyncClient(transport=transport, **kwargs),
    )
    client = AsyncClient(ISSUER + "/", transport=transport, auth=BasicAuth("strategy", "x"))
    calls: dict[str, Refresh] = {
        "async-client": lambda **kw: client.refresh_token_grant(refresh_token="rt1", **kw),
        "helper-issuer": lambda **kw: refresh_authorization(
            refresh_token="rt1", issuer=ISSUER + "/", **kw
        ),
        "helper-metadata": lambda **kw: refresh_authorization(
            refresh_token="rt1", metadata=METADATA, **kw
        ),
        "helper-challenge": lambda **kw: refresh_authorization(
            refresh_token="rt1",
            www_authenticate_header=CHALLENGE,
            http_client=resource_metadata_client(fetches),
            **kw,
        ),
    }
    return transport, fetches, calls


HOSTS = ["async-client", "helper-issuer", "helper-metadata", "helper-challenge"]


def token_requests(transport: FakeTransport) -> list[HttpRequest]:
    return [r for r in transport.requests if r.method == "POST"]


@pytest.mark.parametrize("host", HOSTS)
async def test_row_16_refresh_with_credential(hosts, host):
    transport, _, calls = hosts
    credential = FakeCredential()

    token = await calls[host](credential=credential, resources=["https://api.example.com"])

    assert token.refresh_token == "rt2"
    [req] = token_requests(transport)
    assert req.url == TOKEN_ENDPOINT
    assert req.body is not None
    assert parse_qs(req.body.decode()) == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["rt1"],
        "resource": ["https://api.example.com"],
        "client_assertion": ["assertion"],
        "client_assertion_type": [JWT_BEARER],
    }
    assert req.headers == {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Client-Auth": "credential-header",
    }
    # Every entry mode names the zone the same way, without a trailing slash.
    assert credential.calls == [(ISSUER, TOKEN_ENDPOINT)]


def basic(value: bytes) -> str:
    return "Basic " + base64.b64encode(value).decode()


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(
    ("credentials", "header"),
    [
        pytest.param(
            ("ua:client", "a b+%41"),
            basic(b"ua%3Aclient:a%20b%2B%2541"),
            id="single-zone",
        ),
        pytest.param(
            {
                "https://other.example.com": ("other-id", "other-secret"),
                ISSUER + "/": ("zone-id", "zone-secret"),
            },
            basic(b"zone-id:zone-secret"),
            id="multi-zone",
        ),
    ],
)
async def test_row_16_client_secret_sends_basic_and_no_client_id(
    hosts, host, credentials, header
):
    transport, _, calls = hosts

    await calls[host](credential=ClientSecret(credentials))

    [req] = token_requests(transport)
    assert req.body is not None
    assert parse_qs(req.body.decode()) == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["rt1"],
    }
    assert req.headers["Authorization"] == header


@pytest.mark.parametrize("host", HOSTS)
async def test_unknown_zone_sends_no_token_request(hosts, host):
    transport, _, calls = hosts
    credential = ClientSecret({"https://other.example.com": ("id", "secret")})

    with pytest.raises(ConfigError, match=f"^ClientSecret has no credentials for {ISSUER}$"):
        await calls[host](credential=credential)

    assert token_requests(transport) == []


NOT_BOTH = "Pass either `credential` or `client_id`, not both."
HELPER_NOT_BOTH = "Pass either 'credential' or 'client_id' and 'client_secret', not both"
NEITHER = "Pass 'client_id' or 'credential'"
NO_CAPABILITY = "ExchangeOnlyCredential does not provide client authentication"

HELPER_CASES = [
    (
        "row-16-credential-and-client_id",
        {"credential": FakeCredential(), "client_id": "public-app"},
        HELPER_NOT_BOTH,
    ),
    (
        "credential-and-client_secret",
        {"credential": FakeCredential(), "client_secret": "secret"},
        HELPER_NOT_BOTH,
    ),
    ("neither", {}, NEITHER),
    ("client_secret-only", {"client_secret": "secret"}, NEITHER),
    ("empty-client_id", {"client_id": ""}, NEITHER),
    ("no-client-authentication", {"credential": ExchangeOnlyCredential()}, NO_CAPABILITY),
]


@pytest.mark.parametrize(
    ("host", "kwargs", "message"),
    [
        pytest.param(
            "async-client",
            {"credential": FakeCredential(), "client_id": "public-app"},
            NOT_BOTH,
            id="async-client-row-16-credential-and-client_id",
        ),
        pytest.param(
            "async-client",
            {"credential": ExchangeOnlyCredential()},
            NO_CAPABILITY,
            id="async-client-no-client-authentication",
        ),
        *[
            pytest.param(host, kwargs, message, id=f"{host}-{case}")
            for host in ["helper-issuer", "helper-metadata", "helper-challenge"]
            for case, kwargs, message in HELPER_CASES
        ],
    ],
)
async def test_misconfigured_refresh_sends_no_request(hosts, host, kwargs, message):
    """Discovery is on and the challenge mode fetches resource metadata, so a
    check that ran after either one would leave a request behind."""
    transport, fetches, calls = hosts

    with pytest.raises(ConfigError, match=f"^{re.escape(message)}$"):
        await calls[host](**kwargs)

    assert transport.requests == []
    assert fetches == []
