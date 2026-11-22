"""WorkloadIdentity needs client_id before an interactive grant.

Covers Workload Identity spec-version 4 (unit rows 4d and 4e), row 34 of
Authorization Code with PKCE (spec-version 8) and row 34 of the Device
Authorization Grant (spec-version 5). Every interactive entry point raises
ConfigError before any request: no discovery, no resource metadata fetch, no
browser, no callback listener, and the credential's source is not called.
"""

import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from keycardai.oauth import AsyncClient, AuthorizationServerMetadata, ConfigError
from keycardai.oauth.http._wire import HttpRequest, HttpResponse
from keycardai.oauth.pkce import (
    authenticate,
    complete_authorization,
    refresh_authorization,
)
from keycardai.oauth.server.credentials import WorkloadIdentity
from keycardai.oauth.server.exceptions import WorkloadIdentityConfigurationError
from keycardai.oauth.types.models import DeviceAuthorization

ISSUER = "https://zone.example.com"
TOKEN_ENDPOINT = f"{ISSUER}/oauth/token"
RESOURCE_URL = "https://api.example.com"
RESOURCE_METADATA_URL = f"{RESOURCE_URL}/.well-known/oauth-protected-resource"
CHALLENGE = f'Bearer resource_metadata="{RESOURCE_METADATA_URL}"'
REDIRECT_URI = "https://app.example.com/callback"
JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
MESSAGE = (
    "WorkloadIdentity: client_id is required for interactive grants "
    "(the public ID of a v2 token credential)"
)

pytestmark = pytest.mark.asyncio


class RecordingTransport:
    """Records every request the AsyncClient sends and answers each one."""

    def __init__(self):
        self.requests: list[HttpRequest] = []

    async def request_raw(self, req: HttpRequest, *, timeout: float | None = None):
        self.requests.append(req)
        if req.method == "GET":
            body = {"issuer": ISSUER, "token_endpoint": TOKEN_ENDPOINT}
        else:
            body = {"access_token": "at", "token_type": "Bearer"}
        return HttpResponse(status=200, headers={}, body=json.dumps(body).encode())


class RecordingSource:
    def __init__(self):
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return "platform-token"


class Harness:
    def __init__(self, monkeypatch):
        self.transport = RecordingTransport()
        self.metadata_fetches: list[str] = []
        self.browser: list[str] = []
        self.listeners: list[int] = []
        monkeypatch.setattr(
            "keycardai.oauth.pkce.web.AsyncClient",
            lambda **kwargs: AsyncClient(transport=self.transport, **kwargs),
        )
        monkeypatch.setattr(
            "keycardai.oauth.pkce.client.AsyncClient",
            lambda issuer=None, **kwargs: AsyncClient(
                issuer, transport=self.transport, **kwargs
            ),
        )
        monkeypatch.setattr(
            "keycardai.oauth.pkce.client.webbrowser.open", self.browser.append
        )
        monkeypatch.setattr(
            "keycardai.oauth.pkce.client.OAuthCallbackServer", self.listeners.append
        )

    def client(self) -> AsyncClient:
        return AsyncClient(ISSUER, transport=self.transport)

    def http_client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.metadata_fetches.append(str(request.url))
            return httpx.Response(200, json={"authorization_servers": [ISSUER]})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


DEVICE_AUTHORIZATION = DeviceAuthorization(
    device_code="dc",
    user_code="UC",
    verification_uri=f"{ISSUER}/device",
    expires_in=600,
    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
)

Entry = Callable[[Harness, WorkloadIdentity], Awaitable]

ENTRY_POINTS: dict[str, Entry] = {
    "exchange_authorization_code": lambda h, c: h.client().exchange_authorization_code(
        code="code", redirect_uri=REDIRECT_URI, code_verifier="verifier", credential=c
    ),
    "refresh_token_grant": lambda h, c: h.client().refresh_token_grant(
        refresh_token="rt", credential=c
    ),
    "authenticate-issuer": lambda h, c: authenticate(
        client_id="acr_123", issuer=ISSUER, credential=c
    ),
    "authenticate-challenge": lambda h, c: authenticate(
        client_id="acr_123",
        resource_url=RESOURCE_URL,
        www_authenticate_header=CHALLENGE,
        http_client=h.http_client(),
        allowed_issuers=[ISSUER],
        credential=c,
    ),
    "complete_authorization-issuer": lambda h, c: complete_authorization(
        callback_params={"code": "code", "state": "state"},
        state="state",
        code_verifier="verifier",
        redirect_uri=REDIRECT_URI,
        issuer=ISSUER,
        credential=c,
    ),
    "complete_authorization-challenge": lambda h, c: complete_authorization(
        callback_params={"code": "code", "state": "state"},
        state="state",
        code_verifier="verifier",
        redirect_uri=REDIRECT_URI,
        www_authenticate_header=CHALLENGE,
        http_client=h.http_client(),
        allowed_issuers=[ISSUER],
        credential=c,
    ),
    "refresh_authorization-issuer": lambda h, c: refresh_authorization(
        refresh_token="rt", issuer=ISSUER, credential=c
    ),
    "refresh_authorization-metadata": lambda h, c: refresh_authorization(
        refresh_token="rt",
        metadata=AuthorizationServerMetadata(issuer=ISSUER, token_endpoint=TOKEN_ENDPOINT),
        credential=c,
    ),
    "refresh_authorization-challenge": lambda h, c: refresh_authorization(
        refresh_token="rt",
        www_authenticate_header=CHALLENGE,
        http_client=h.http_client(),
        allowed_issuers=[ISSUER],
        credential=c,
    ),
    "request_device_authorization": lambda h, c: h.client().request_device_authorization(
        credential=c
    ),
    "poll_device_token": lambda h, c: h.client().poll_device_token(
        DEVICE_AUTHORIZATION, credential=c
    ),
    "wait_for_device_token": lambda h, c: h.client().wait_for_device_token(
        DEVICE_AUTHORIZATION, credential=c
    ),
}


@pytest.mark.parametrize("entry", ENTRY_POINTS.values(), ids=ENTRY_POINTS.keys())
async def test_interactive_grant_without_client_id_sends_nothing(monkeypatch, entry):
    """Rows 4d and 34: ConfigError before any request; the source is not called."""
    harness = Harness(monkeypatch)
    source = RecordingSource()

    with pytest.raises(ConfigError, match=f"^{re.escape(MESSAGE)}$") as error:
        await entry(harness, WorkloadIdentity(source))

    assert not isinstance(error.value, WorkloadIdentityConfigurationError)
    assert harness.transport.requests == []
    assert harness.metadata_fetches == []
    assert harness.browser == []
    assert harness.listeners == []
    assert source.calls == 0


@pytest.mark.parametrize(
    "entry",
    [
        ENTRY_POINTS["exchange_authorization_code"],
        ENTRY_POINTS["refresh_token_grant"],
        ENTRY_POINTS["refresh_authorization-issuer"],
        ENTRY_POINTS["poll_device_token"],
    ],
    ids=[
        "exchange_authorization_code",
        "refresh_token_grant",
        "refresh_authorization-issuer",
        "poll_device_token",
    ],
)
async def test_interactive_grant_with_client_id_sends_it(monkeypatch, entry):
    """Row 4d: with client_id set, the check passes and the grant sends it."""
    harness = Harness(monkeypatch)
    source = RecordingSource()

    await entry(harness, WorkloadIdentity(source, client_id="acr_123"))

    [post] = [r for r in harness.transport.requests if r.method == "POST"]
    body = post.body.decode() if isinstance(post.body, bytes) else str(post.body)
    assert "client_id=acr_123" in body
    assert "client_assertion=platform-token" in body
    assert source.calls == 1


async def test_token_exchange_without_client_id_is_not_checked():
    """Row 4e: token exchange carries the assertion and no client_id."""
    source = RecordingSource()
    credential = WorkloadIdentity(source)

    request = await credential.prepare_token_exchange_request(
        client=None, subject_token="subject", resource=RESOURCE_URL
    )

    assert request.client_assertion == "platform-token"
    assert request.client_assertion_type == JWT_BEARER
    assert request.client_id is None
    assert source.calls == 1


async def test_client_authentication_without_client_id_is_not_checked():
    """Row 4e: client authentication itself does not check, so the
    client_credentials grant still runs without client_id."""
    credential = WorkloadIdentity(RecordingSource())

    auth = await credential.client_authentication(issuer=ISSUER, endpoint=TOKEN_ENDPOINT)

    assert auth.form_fields == {
        "client_assertion": "platform-token",
        "client_assertion_type": JWT_BEARER,
    }
