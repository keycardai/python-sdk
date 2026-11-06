"""Refresh with a credential (spec-version 5 of Authorization Code with PKCE).

Covers row 16 of the spec's unit test table on both hosts:
AsyncClient.refresh_token_grant and pkce.refresh_authorization. The
credential's client authentication replaces the client's auth strategy.
"""

import json
from urllib.parse import parse_qs

import pytest

from keycardai.oauth import (
    AsyncClient,
    AuthorizationServerMetadata,
    BasicAuth,
    ClientAuthentication,
    ClientConfig,
    ConfigError,
    Endpoints,
)
from keycardai.oauth.http._wire import HttpRequest, HttpResponse
from keycardai.oauth.pkce import refresh_authorization

ISSUER = "https://zone.example.com"
TOKEN_ENDPOINT = "https://zone.example.com/oauth/token"
JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
METADATA = AuthorizationServerMetadata(issuer=ISSUER, token_endpoint=TOKEN_ENDPOINT)

pytestmark = pytest.mark.asyncio


class FakeTransport:
    def __init__(self):
        self.requests: list[HttpRequest] = []

    async def request_raw(self, req: HttpRequest, *, timeout: float | None = None):
        self.requests.append(req)
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


EXPECTED_BODY = {
    "grant_type": ["refresh_token"],
    "refresh_token": ["rt1"],
    "resource": ["https://api.example.com"],
    "client_assertion": ["assertion"],
    "client_assertion_type": [JWT_BEARER],
}


def assert_credential_request(transport: FakeTransport, credential: FakeCredential):
    [req] = transport.requests
    assert req.url == TOKEN_ENDPOINT
    assert req.body is not None
    assert parse_qs(req.body.decode()) == EXPECTED_BODY
    assert req.headers["X-Client-Auth"] == "credential-header"
    assert "Authorization" not in req.headers
    assert credential.calls == [(ISSUER, TOKEN_ENDPOINT)]


def make_client(transport: FakeTransport, **kwargs) -> AsyncClient:
    return AsyncClient(
        ISSUER,
        transport=transport,
        endpoints=Endpoints(token=TOKEN_ENDPOINT),
        config=ClientConfig(enable_metadata_discovery=False, auto_register_client=False),
        **kwargs,
    )


async def test_row_16_async_client_refresh_with_credential():
    transport = FakeTransport()
    credential = FakeCredential()
    client = make_client(transport, auth=BasicAuth("client-strategy", "unused"))

    token = await client.refresh_token_grant(
        refresh_token="rt1", credential=credential, resources=["https://api.example.com"]
    )

    assert token.refresh_token == "rt2"
    assert_credential_request(transport, credential)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        pytest.param(
            {"credential": FakeCredential(), "client_id": "public-app"},
            "not both",
            id="row-16-credential-and-client_id",
        ),
        pytest.param(
            {"credential": ExchangeOnlyCredential()},
            "does not provide client authentication",
            id="no-client-authentication",
        ),
    ],
)
async def test_async_client_refresh_misconfigured(kwargs, message):
    transport = FakeTransport()

    with pytest.raises(ConfigError, match=message):
        await make_client(transport).refresh_token_grant(refresh_token="rt1", **kwargs)

    assert transport.requests == []


async def test_row_16_refresh_authorization_with_credential(monkeypatch):
    transport = FakeTransport()
    credential = FakeCredential()
    monkeypatch.setattr(
        "keycardai.oauth.pkce.web.AsyncClient",
        lambda **kwargs: AsyncClient(transport=transport, **kwargs),
    )

    token = await refresh_authorization(
        refresh_token="rt1",
        credential=credential,
        metadata=METADATA,
        resources=["https://api.example.com"],
    )

    assert token.refresh_token == "rt2"
    assert_credential_request(transport, credential)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        pytest.param(
            {"credential": FakeCredential(), "client_id": "public-app"},
            "not both",
            id="row-16-credential-and-client_id",
        ),
        pytest.param(
            {"credential": FakeCredential(), "client_secret": "secret"},
            "not both",
            id="credential-and-client_secret",
        ),
        pytest.param({}, "Pass 'client_id' or 'credential'", id="neither"),
        pytest.param(
            {"credential": ExchangeOnlyCredential()},
            "does not provide client authentication",
            id="no-client-authentication",
        ),
    ],
)
async def test_refresh_authorization_misconfigured(monkeypatch, kwargs, message):
    def no_client(**_):
        raise AssertionError("no client is built for a configuration error")

    monkeypatch.setattr("keycardai.oauth.pkce.web.AsyncClient", no_client)

    with pytest.raises(ConfigError, match=message):
        await refresh_authorization(refresh_token="rt1", issuer=ISSUER, **kwargs)
