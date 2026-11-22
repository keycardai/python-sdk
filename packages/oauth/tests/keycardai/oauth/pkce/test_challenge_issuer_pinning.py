"""Challenge-resolved issuer pinning (Authorization Code with PKCE, spec-version 7).

Covers unit rows 23 to 28: client authentication goes to an issuer resolved
from a WWW-Authenticate challenge only when the caller lists it in
``allowed_issuers``. Each challenge entry point is driven up to the point
where it would contact the resolved issuer, which the patched
``AsyncClient`` records and stops.
"""

import httpx
import pytest

from keycardai.oauth import ClientAuthentication
from keycardai.oauth.exceptions import ConfigError
from keycardai.oauth.pkce import (
    authenticate,
    begin_authorization,
    complete_authorization,
    refresh_authorization,
)

ISSUER = "https://zone.example.com"
RESOURCE_METADATA_URL = "https://api.example.com/.well-known/oauth-protected-resource"
CHALLENGE = f'Bearer resource_metadata="{RESOURCE_METADATA_URL}"'
STATE = "stored-state"

pytestmark = pytest.mark.asyncio


class ReachedIssuer(Exception):
    """Raised by the patched AsyncClient: the flow was about to contact the issuer."""

    def __init__(self, issuer: str, auth):
        super().__init__(issuer)
        self.issuer = issuer
        self.auth = auth


class FakeCredential:
    async def client_authentication(self, *, issuer: str, endpoint: str) -> ClientAuthentication:
        return ClientAuthentication(headers={}, form_fields={"client_assertion": "platform-token"})


@pytest.fixture
def fetches(monkeypatch) -> list[str]:
    def reached(*, issuer, auth=None, **_):
        raise ReachedIssuer(issuer, auth)

    monkeypatch.setattr("keycardai.oauth.pkce.client.AsyncClient", reached)
    monkeypatch.setattr("keycardai.oauth.pkce.web.AsyncClient", reached)
    return []


def metadata_client(fetches: list[str], issuer: str = ISSUER + "/") -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        fetches.append(str(request.url))
        return httpx.Response(200, json={"authorization_servers": [issuer]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def entry(name: str, fetches: list[str], **kw):
    """Call one challenge entry point with the given client auth and allow-list."""
    common = {"www_authenticate_header": CHALLENGE, "http_client": metadata_client(fetches)}
    if name == "authenticate":
        if "client_id" not in kw:
            kw["client_id"] = "app"
        return authenticate(resource_url="https://api.example.com", **common, **kw)
    if name == "complete":
        if "credential" not in kw:
            kw.setdefault("client_id", "app")
        return complete_authorization(
            callback_params={"code": "c", "state": STATE},
            state=STATE,
            code_verifier="v",
            redirect_uri="https://app.example.com/callback",
            **common,
            **kw,
        )
    if name == "refresh":
        if "credential" not in kw:
            kw.setdefault("client_id", "app")
        return refresh_authorization(refresh_token="rt", **common, **kw)
    raise AssertionError(name)


AUTH_ENTRIES = ["authenticate", "complete", "refresh"]
CLIENT_AUTH = {
    "client_secret": lambda: {"client_secret": "shh"},
    "credential": lambda: {"credential": FakeCredential()},
}


@pytest.mark.parametrize("entry_name", AUTH_ENTRIES)
@pytest.mark.parametrize("auth", list(CLIENT_AUTH))
@pytest.mark.parametrize("allowed", [None, []])
async def test_rows_23_24_client_auth_without_allowed_issuers(fetches, entry_name, auth, allowed):
    with pytest.raises(ConfigError, match="allowed_issuers"):
        await entry(entry_name, fetches, allowed_issuers=allowed, **CLIENT_AUTH[auth]())
    assert fetches == []


@pytest.mark.parametrize("entry_name", AUTH_ENTRIES)
@pytest.mark.parametrize("auth", [*CLIENT_AUTH, "public"])
async def test_rows_25_27_resolved_issuer_not_allowed(fetches, entry_name, auth):
    client_auth = CLIENT_AUTH[auth]() if auth != "public" else {}
    with pytest.raises(ConfigError, match=f"'{ISSUER}', which is not in 'allowed_issuers'"):
        await entry(
            entry_name,
            fetches,
            allowed_issuers=["https://trusted.example.com"],
            **client_auth,
        )
    # Only the protected resource metadata was fetched; the patched
    # AsyncClient (the resolved issuer) was never reached.
    assert fetches == [RESOURCE_METADATA_URL]


async def test_rows_25_27_begin_checks_allowed_issuers(fetches):
    with pytest.raises(ConfigError, match="not in 'allowed_issuers'"):
        await begin_authorization(
            client_id="app",
            redirect_uri="https://app.example.com/callback",
            resources=["https://api.example.com"],
            www_authenticate_header=CHALLENGE,
            http_client=metadata_client(fetches),
            allowed_issuers=["https://trusted.example.com"],
        )
    assert fetches == [RESOURCE_METADATA_URL]


@pytest.mark.parametrize("entry_name", AUTH_ENTRIES)
@pytest.mark.parametrize("auth", list(CLIENT_AUTH))
@pytest.mark.parametrize("allowed", [ISSUER, ISSUER + "/"])
async def test_row_26_allowed_issuer_proceeds(fetches, entry_name, auth, allowed):
    with pytest.raises(ReachedIssuer) as reached:
        await entry(entry_name, fetches, allowed_issuers=[allowed], **CLIENT_AUTH[auth]())
    assert reached.value.issuer == ISSUER
    assert fetches == [RESOURCE_METADATA_URL]


@pytest.mark.parametrize("entry_name", AUTH_ENTRIES)
async def test_row_27_public_client_without_allowed_issuers_proceeds(fetches, entry_name):
    with pytest.raises(ReachedIssuer) as reached:
        await entry(entry_name, fetches)
    assert reached.value.issuer == ISSUER


async def test_row_28_issuer_direct_client_auth_needs_no_allowed_issuers(fetches):
    with pytest.raises(ReachedIssuer) as reached:
        await refresh_authorization(refresh_token="rt", issuer=ISSUER, client_id="app", client_secret="shh")
    assert reached.value.issuer == ISSUER
    with pytest.raises(ReachedIssuer):
        await authenticate(client_id="app", issuer=ISSUER, client_secret="shh")
