"""Wire-level tests for client assertions on Client/AsyncClient.impersonate().

An application whose credential is a workload identity has no client secret
and authenticates the substitute-user exchange in the request body, the same
way exchange_token() and client_credentials_grant() accept it.
"""

import json
from urllib.parse import parse_qs

import pytest

from keycardai.oauth import AsyncClient, Client, ClientConfig
from keycardai.oauth.http._wire import HttpRequest, HttpResponse
from keycardai.oauth.types.models import TokenExchangeRequest, TokenType

ZONE = "https://zone.keycard.cloud"
RESOURCE = "https://api.example.com"
ASSERTION = "header.payload.signature"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

TOKEN_RESPONSE = json.dumps(
    {"access_token": "issued_token", "token_type": "Bearer", "expires_in": 3600}
).encode()


class FakeTransport:
    def __init__(self):
        self.requests: list[HttpRequest] = []

    def request_raw(self, request: HttpRequest, timeout=None) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(
            status=200, headers={"Content-Type": "application/json"}, body=TOKEN_RESPONSE
        )


class FakeAsyncTransport(FakeTransport):
    async def request_raw(self, request: HttpRequest, timeout=None) -> HttpResponse:
        return super().request_raw(request, timeout)


def _form(request: HttpRequest) -> dict[str, list[str]]:
    return parse_qs(request.body.decode(), keep_blank_values=True)


def _make_client(transport) -> Client:
    return Client(ZONE, transport=transport, config=ClientConfig(enable_metadata_discovery=False))


def _make_async_client(transport) -> AsyncClient:
    return AsyncClient(
        ZONE, transport=transport, config=ClientConfig(enable_metadata_discovery=False)
    )


class TestSyncImpersonateClientAssertion:
    def test_assertion_fields_reach_the_form_body(self):
        transport = FakeTransport()
        client = _make_client(transport)

        response = client.impersonate(
            user_identifier="user@example.com",
            resource=RESOURCE,
            client_assertion=ASSERTION,
            client_assertion_type=ASSERTION_TYPE,
        )

        assert response.access_token == "issued_token"
        form = _form(transport.requests[0])
        assert form["client_assertion"] == [ASSERTION]
        assert form["client_assertion_type"] == [ASSERTION_TYPE]
        assert form["subject_token_type"] == [TokenType.SUBSTITUTE_USER.value]
        assert form["resource"] == [RESOURCE]
        assert "Authorization" not in transport.requests[0].headers

    def test_omitting_the_assertion_is_unchanged(self):
        transport = FakeTransport()
        client = _make_client(transport)

        client.impersonate(user_identifier="user@example.com", resource=RESOURCE)
        client.exchange_token(
            TokenExchangeRequest(
                subject_token=transport.requests[0].body.decode().split("subject_token=")[1].split("&")[0],
                subject_token_type=TokenType.SUBSTITUTE_USER,
                resource=RESOURCE,
            )
        )

        impersonated, baseline = transport.requests
        assert impersonated.body == baseline.body
        assert b"client_assertion" not in impersonated.body


class TestAsyncImpersonateClientAssertion:
    @pytest.mark.asyncio
    async def test_assertion_fields_reach_the_form_body(self):
        transport = FakeAsyncTransport()
        client = _make_async_client(transport)

        response = await client.impersonate(
            user_identifier="user@example.com",
            resource=RESOURCE,
            client_assertion=ASSERTION,
            client_assertion_type=ASSERTION_TYPE,
        )

        assert response.access_token == "issued_token"
        form = _form(transport.requests[0])
        assert form["client_assertion"] == [ASSERTION]
        assert form["client_assertion_type"] == [ASSERTION_TYPE]
        assert form["subject_token_type"] == [TokenType.SUBSTITUTE_USER.value]
        assert "Authorization" not in transport.requests[0].headers

    @pytest.mark.asyncio
    async def test_omitting_the_assertion_is_unchanged(self):
        transport = FakeAsyncTransport()
        client = _make_async_client(transport)

        await client.impersonate(user_identifier="user@example.com", resource=RESOURCE)
        await client.exchange_token(
            TokenExchangeRequest(
                subject_token=transport.requests[0].body.decode().split("subject_token=")[1].split("&")[0],
                subject_token_type=TokenType.SUBSTITUTE_USER,
                resource=RESOURCE,
            )
        )

        impersonated, baseline = transport.requests
        assert impersonated.body == baseline.body
        assert b"client_assertion" not in impersonated.body
