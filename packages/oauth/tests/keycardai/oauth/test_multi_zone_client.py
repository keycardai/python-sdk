"""Wire-level tests for zone-aware authentication on the low-level clients.

A multi-zone ClientSecret holds per-issuer credentials. These tests assert
that Client/AsyncClient token operations send the Basic Authorization header
for the selected issuer, and fail closed for unknown or missing issuers.
"""

import base64
import json

import pytest

from keycardai.oauth import AsyncClient, Client, ClientConfig
from keycardai.oauth.http._wire import HttpRequest, HttpResponse
from keycardai.oauth.server.credentials import ClientSecret

ZONE1 = "https://zone1.keycard.cloud"
ZONE2 = "https://zone2.keycard.cloud"

CREDENTIALS = {
    ZONE1: ("client_id_1", "client_secret_1"),
    ZONE2: ("client_id_2", "client_secret_2"),
}

TOKEN_RESPONSE = json.dumps(
    {
        "access_token": "issued_token",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
).encode()


def _basic_header(client_id: str, client_secret: str) -> str:
    encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return f"Basic {encoded}"


class FakeTransport:
    """Captures requests and returns a canned token response (sync)."""

    def __init__(self):
        self.requests: list[HttpRequest] = []

    def request_raw(self, request: HttpRequest, timeout=None) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=TOKEN_RESPONSE,
        )


class FakeAsyncTransport:
    """Captures requests and returns a canned token response (async)."""

    def __init__(self):
        self.requests: list[HttpRequest] = []

    async def request_raw(self, request: HttpRequest, timeout=None) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=TOKEN_RESPONSE,
        )


def make_client(transport) -> Client:
    return Client(
        ZONE1,
        auth=ClientSecret(CREDENTIALS).get_http_client_auth(),
        transport=transport,
        config=ClientConfig(enable_metadata_discovery=False),
    )


def make_async_client(transport) -> AsyncClient:
    return AsyncClient(
        ZONE1,
        auth=ClientSecret(CREDENTIALS).get_http_client_auth(),
        transport=transport,
        config=ClientConfig(enable_metadata_discovery=False),
    )


class TestSyncClientMultiZone:
    def test_exchange_token_sends_basic_header_for_selected_issuer(self):
        transport = FakeTransport()
        client = make_client(transport)

        response = client.exchange_token(
            subject_token="subject",
            issuer=ZONE2,
        )

        assert response.access_token == "issued_token"
        assert len(transport.requests) == 1
        sent = transport.requests[0]
        assert sent.headers["Authorization"] == _basic_header(
            "client_id_2", "client_secret_2"
        )
        # The issuer selector is not a wire field.
        assert b"issuer" not in sent.body

    def test_exchange_token_defaults_to_client_issuer(self):
        transport = FakeTransport()
        client = make_client(transport)

        client.exchange_token(subject_token="subject")

        sent = transport.requests[0]
        assert sent.headers["Authorization"] == _basic_header(
            "client_id_1", "client_secret_1"
        )

    def test_exchange_token_with_request_object_and_issuer(self):
        from keycardai.oauth.types.models import TokenExchangeRequest

        transport = FakeTransport()
        client = make_client(transport)

        request = TokenExchangeRequest(subject_token="subject")
        client.exchange_token(request, issuer=ZONE2)

        sent = transport.requests[0]
        assert sent.headers["Authorization"] == _basic_header(
            "client_id_2", "client_secret_2"
        )

    def test_exchange_token_unknown_issuer_fails_closed(self):
        transport = FakeTransport()
        client = make_client(transport)

        with pytest.raises(KeyError, match="not configured"):
            client.exchange_token(
                subject_token="subject",
                issuer="https://unknown.keycard.cloud",
            )
        assert transport.requests == []

    def test_exchange_token_rejects_request_and_kwargs(self):
        from keycardai.oauth.types.models import TokenExchangeRequest

        transport = FakeTransport()
        client = make_client(transport)

        with pytest.raises(TypeError):
            client.exchange_token(
                TokenExchangeRequest(subject_token="subject"),
                issuer=ZONE2,
                subject_token="subject",
            )

    def test_client_credentials_grant_issuer_selector(self):
        transport = FakeTransport()
        client = make_client(transport)

        response = client.client_credentials_grant(
            resource="https://api.example.com",
            issuer=ZONE2,
        )

        assert response.access_token == "issued_token"
        sent = transport.requests[0]
        assert sent.headers["Authorization"] == _basic_header(
            "client_id_2", "client_secret_2"
        )

    def test_client_credentials_grant_unknown_issuer_fails_closed(self):
        transport = FakeTransport()
        client = make_client(transport)

        with pytest.raises(KeyError, match="not configured"):
            client.client_credentials_grant(
                resource="https://api.example.com",
                issuer="https://unknown.keycard.cloud",
            )
        assert transport.requests == []


class TestAsyncClientMultiZone:
    @pytest.mark.asyncio
    async def test_exchange_token_sends_basic_header_for_selected_issuer(self):
        transport = FakeAsyncTransport()
        client = make_async_client(transport)

        response = await client.exchange_token(
            subject_token="subject",
            issuer=ZONE2,
        )

        assert response.access_token == "issued_token"
        sent = transport.requests[0]
        assert sent.headers["Authorization"] == _basic_header(
            "client_id_2", "client_secret_2"
        )

    @pytest.mark.asyncio
    async def test_client_credentials_grant_issuer_selector(self):
        transport = FakeAsyncTransport()
        client = make_async_client(transport)

        await client.client_credentials_grant(
            resource="https://api.example.com",
            issuer=ZONE1,
        )

        sent = transport.requests[0]
        assert sent.headers["Authorization"] == _basic_header(
            "client_id_1", "client_secret_1"
        )

    @pytest.mark.asyncio
    async def test_exchange_token_unknown_issuer_fails_closed(self):
        transport = FakeAsyncTransport()
        client = make_async_client(transport)

        with pytest.raises(KeyError, match="not configured"):
            await client.exchange_token(
                subject_token="subject",
                issuer="https://unknown.keycard.cloud",
            )
        assert transport.requests == []
