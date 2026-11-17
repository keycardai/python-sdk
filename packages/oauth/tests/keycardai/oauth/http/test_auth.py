"""Unit tests for OAuth 2.0 client authentication strategies."""

import base64
import json
from urllib.parse import unquote, unquote_plus

import pytest

from keycardai.oauth import (
    AsyncClient,
    AuthorizationServerMetadata,
    Client,
    ClientConfig,
)
from keycardai.oauth.http._wire import HttpRequest, HttpResponse
from keycardai.oauth.http.auth import (
    BasicAuth,
    BearerAuth,
    MultiZoneBasicAuth,
    NoneAuth,
)
from keycardai.oauth.pkce import complete_authorization, refresh_authorization
from keycardai.oauth.server.credentials import ClientSecret


def _basic_header(client_id: str, client_secret: str) -> str:
    encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return f"Basic {encoded}"


def _decode_basic(header: str) -> tuple[str, str]:
    scheme, _, encoded = header.partition(" ")
    assert scheme == "Basic"
    client_id, colon, client_secret = base64.b64decode(encoded).decode("ascii").partition(":")
    assert colon == ":"
    return client_id, client_secret


# ClientSecret spec row 8 (spec-version 3). RFC 6749 Section 2.3.1 requires each
# half to be encoded before it goes into the Basic credential.
SPEC_ROW_8 = ("ua:client", "a b+%41", "ua%3Aclient", "a%20b%2B%2541")


class TestBasicEncoding:
    @pytest.mark.parametrize(
        ("client_id", "client_secret", "wire_id", "wire_secret"),
        [
            pytest.param(*SPEC_ROW_8, id="client-secret-row-8"),
            pytest.param("plain-id_1.2~", "Secret09", "plain-id_1.2~", "Secret09", id="unreserved"),
            pytest.param("id", "ab+/c=", "id", "ab%2B%2Fc%3D", id="base64-characters"),
            pytest.param("ïd", "秘密", "%C3%AFd", "%E7%A7%98%E5%AF%86", id="utf-8"),
            pytest.param("id", "s:t", "id", "s%3At", id="colon-in-secret"),
            pytest.param("id", "!*'()", "id", "%21%2A%27%28%29", id="sub-delims"),
        ],
    )
    def test_each_half_is_percent_encoded(self, client_id, client_secret, wire_id, wire_secret):
        header = BasicAuth(client_id, client_secret).apply_headers()["Authorization"]

        assert header == _basic_header(wire_id, wire_secret)
        # A Keycard zone percent-decodes each half (decodeURIComponent). An
        # RFC 6749 Appendix B server form-decodes it. Both get the input back.
        assert tuple(unquote(v) for v in _decode_basic(header)) == (client_id, client_secret)
        assert tuple(unquote_plus(v) for v in _decode_basic(header)) == (
            client_id,
            client_secret,
        )

    def test_multi_zone_encodes_the_selected_zone(self):
        auth = MultiZoneBasicAuth({"https://zone1.keycard.cloud": SPEC_ROW_8[:2]})

        assert auth.apply_headers("https://zone1.keycard.cloud") == {
            "Authorization": _basic_header(*SPEC_ROW_8[2:])
        }


TOKEN_BODY = json.dumps({"access_token": "at", "token_type": "Bearer"}).encode()


class _SyncTransport:
    def __init__(self):
        self.requests: list[HttpRequest] = []

    def request_raw(self, request: HttpRequest, timeout=None) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(status=200, headers={}, body=TOKEN_BODY)


class _AsyncTransport(_SyncTransport):
    async def request_raw(self, request: HttpRequest, timeout=None) -> HttpResponse:  # type: ignore[override]
        return super().request_raw(request, timeout)


GRANTS = [
    pytest.param(lambda c: c.exchange_token(subject_token="st"), id="token-exchange"),
    pytest.param(lambda c: c.client_credentials_grant(), id="client-credentials"),
    pytest.param(
        lambda c: c.exchange_authorization_code(
            code="code", redirect_uri="http://localhost/cb", code_verifier="v" * 43
        ),
        id="authorization-code",
    ),
    pytest.param(lambda c: c.refresh_token_grant(refresh_token="rt"), id="refresh"),
]


AUTH = [
    pytest.param(lambda: BasicAuth(*SPEC_ROW_8[:2]), id="BasicAuth"),
    pytest.param(
        lambda: ClientSecret(SPEC_ROW_8[:2]).get_http_client_auth(), id="ClientSecret"
    ),
]

METADATA = AuthorizationServerMetadata(
    issuer="https://zone1.keycard.cloud",
    token_endpoint="https://zone1.keycard.cloud/oauth/token",
)

# The PKCE helpers take a raw client ID and secret and build BasicAuth.
HELPERS = [
    pytest.param(
        lambda: refresh_authorization(
            refresh_token="rt",
            client_id=SPEC_ROW_8[0],
            client_secret=SPEC_ROW_8[1],
            metadata=METADATA,
        ),
        id="refresh_authorization",
    ),
    pytest.param(
        lambda: complete_authorization(
            callback_params={"code": "code", "state": "st"},
            state="st",
            code_verifier="v" * 43,
            client_id=SPEC_ROW_8[0],
            client_secret=SPEC_ROW_8[1],
            redirect_uri="http://localhost/cb",
            metadata=METADATA,
        ),
        id="complete_authorization",
    ),
]


class TestBasicEncodingOnEveryGrant:
    """ClientSecret spec row 8: every grant that sends a raw client ID and
    secret with HTTP Basic sends the same encoded header."""

    CONFIG = ClientConfig(enable_metadata_discovery=False, auto_register_client=False)

    @pytest.mark.parametrize("auth", AUTH)
    @pytest.mark.parametrize("grant", GRANTS)
    def test_sync_client(self, grant, auth):
        transport = _SyncTransport()
        client = Client(
            "https://zone1.keycard.cloud",
            auth=auth(),
            transport=transport,
            config=self.CONFIG,
        )

        grant(client)

        [request] = transport.requests
        assert request.headers["Authorization"] == _basic_header(*SPEC_ROW_8[2:])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("auth", AUTH)
    @pytest.mark.parametrize("grant", GRANTS)
    async def test_async_client(self, grant, auth):
        transport = _AsyncTransport()
        client = AsyncClient(
            "https://zone1.keycard.cloud",
            auth=auth(),
            transport=transport,
            config=self.CONFIG,
        )

        await grant(client)

        [request] = transport.requests
        assert request.headers["Authorization"] == _basic_header(*SPEC_ROW_8[2:])


class TestSingleCredentialStrategies:
    def test_none_auth_ignores_issuer_selector(self):
        auth = NoneAuth()
        assert auth.apply_headers() == {}
        assert auth.apply_headers("https://zone1.keycard.cloud") == {}

    def test_basic_auth_ignores_issuer_selector(self):
        auth = BasicAuth("client", "secret")
        expected = {"Authorization": _basic_header("client", "secret")}
        assert auth.apply_headers() == expected
        assert auth.apply_headers("https://zone1.keycard.cloud") == expected

    def test_bearer_auth_ignores_issuer_selector(self):
        auth = BearerAuth("token123")
        expected = {"Authorization": "Bearer token123"}
        assert auth.apply_headers() == expected
        assert auth.apply_headers("https://zone1.keycard.cloud") == expected


class TestMultiZoneBasicAuth:
    def make_auth(self) -> MultiZoneBasicAuth:
        return MultiZoneBasicAuth({
            "https://zone1.keycard.cloud": ("client_id_1", "client_secret_1"),
            "https://zone2.keycard.cloud": ("client_id_2", "client_secret_2"),
        })

    def test_apply_headers_selects_issuer_credentials(self):
        auth = self.make_auth()
        assert auth.apply_headers("https://zone1.keycard.cloud") == {
            "Authorization": _basic_header("client_id_1", "client_secret_1")
        }
        assert auth.apply_headers("https://zone2.keycard.cloud") == {
            "Authorization": _basic_header("client_id_2", "client_secret_2")
        }

    def test_apply_headers_without_issuer_raises(self):
        auth = self.make_auth()
        with pytest.raises(ValueError, match="requires an issuer"):
            auth.apply_headers()
        with pytest.raises(ValueError, match="requires an issuer"):
            auth.apply_headers(None)

    def test_apply_headers_unknown_issuer_fails_closed(self):
        auth = self.make_auth()
        with pytest.raises(KeyError, match="not configured"):
            auth.apply_headers("https://unknown.keycard.cloud")

    def test_trailing_slash_normalization(self):
        auth = MultiZoneBasicAuth({
            "https://zone1.keycard.cloud/": ("client_id_1", "client_secret_1"),
        })
        expected = {
            "Authorization": _basic_header("client_id_1", "client_secret_1")
        }
        assert auth.apply_headers("https://zone1.keycard.cloud") == expected
        assert auth.apply_headers("https://zone1.keycard.cloud/") == expected
        assert auth.has_issuer("https://zone1.keycard.cloud")
        assert auth.has_issuer("https://zone1.keycard.cloud/")
        assert auth.get_configured_issuers() == ["https://zone1.keycard.cloud"]

    def test_get_auth_for_issuer(self):
        auth = self.make_auth()
        basic = auth.get_auth_for_issuer("https://zone1.keycard.cloud")
        assert isinstance(basic, BasicAuth)
        assert basic.client_id == "client_id_1"
        with pytest.raises(KeyError, match="not configured"):
            auth.get_auth_for_issuer("https://unknown.keycard.cloud")

    def test_has_issuer(self):
        auth = self.make_auth()
        assert auth.has_issuer("https://zone1.keycard.cloud")
        assert not auth.has_issuer("https://unknown.keycard.cloud")

    def test_empty_credentials_rejected(self):
        with pytest.raises(ValueError):
            MultiZoneBasicAuth({})
        with pytest.raises(ValueError):
            MultiZoneBasicAuth({"https://zone1.keycard.cloud": ("", "secret")})
        with pytest.raises(ValueError):
            MultiZoneBasicAuth({"https://zone1.keycard.cloud": ("id", "")})
        with pytest.raises(ValueError):
            MultiZoneBasicAuth({"": ("id", "secret")})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("helper", HELPERS)
    async def test_pkce_helpers(self, monkeypatch, helper):
        transport = _AsyncTransport()
        monkeypatch.setattr(
            "keycardai.oauth.pkce.web.AsyncClient",
            lambda **kwargs: AsyncClient(transport=transport, **kwargs),
        )

        await helper()

        [request] = transport.requests
        assert request.headers["Authorization"] == _basic_header(*SPEC_ROW_8[2:])
