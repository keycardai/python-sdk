"""Client authentication on ClientSecret, WebIdentity, and WorkloadIdentity.

Test ids name the credential spec and its unit test row
(specs/application-credentials/*.md in the Keycard SDK spec).
"""

import base64
import json
import re
import uuid
from unittest.mock import MagicMock

import pytest
from joserfc import jwt as jose_jwt
from joserfc.jwk import KeySet

from keycardai.oauth import (
    ClientAuthentication,
    ClientConfig,
    ConfigError,
)
from keycardai.oauth.client_auth import require_client_authenticator
from keycardai.oauth.server.credentials import (
    ClientSecret,
    WebIdentity,
    WorkloadIdentity,
)
from keycardai.oauth.server.exceptions import (
    WebIdentityConfigurationError,
    WorkloadIdentityRuntimeError,
)

ISSUER = "https://zone1.keycard.cloud"
DEVICE_ENDPOINT = "https://zone1.keycard.cloud/oauth/device_authorization"
TOKEN_ENDPOINT = "https://zone1.keycard.cloud/oauth/token"
JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
NO_CLIENT_ID = "WebIdentity needs auth_info with 'resource_client_id' or the client_id option"

pytestmark = pytest.mark.asyncio


def exactly(message: str) -> str:
    return f"^{re.escape(message)}$"


def basic(client_id: str, secret: str) -> str:
    """The Basic header for values that are already percent-encoded."""
    return "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()


def web_identity(tmp_path, **kwargs) -> WebIdentity:
    return WebIdentity(server_name="server", storage_dir=str(tmp_path), **kwargs)


def decode(assertion: str) -> tuple[dict, dict]:
    header, payload, _ = assertion.split(".")

    def part(segment: str) -> dict:
        return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))

    return part(header), part(payload)


# The capability


class ThirdPartyCredential:
    """A credential implemented outside the SDK, with client authentication."""

    async def client_authentication(
        self, *, issuer: str, endpoint: str
    ) -> ClientAuthentication:
        return ClientAuthentication()


class ExchangeOnlyCredential:
    """A credential implemented outside the SDK, without client authentication."""

    async def prepare_token_exchange_request(self, client, subject_token, resource, auth_info=None):
        raise AssertionError("not used")


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda tmp: ClientSecret(("cid", "secret")), id="ClientSecret"),
        pytest.param(lambda tmp: web_identity(tmp, client_id="cid"), id="WebIdentity"),
        pytest.param(lambda tmp: WorkloadIdentity(lambda: "token"), id="WorkloadIdentity"),
        pytest.param(lambda tmp: ThirdPartyCredential(), id="third-party"),
    ],
)
async def test_credentials_provide_client_authentication(tmp_path, make):
    credential = make(tmp_path)

    assert require_client_authenticator(credential) is credential


@pytest.mark.parametrize(
    ("make", "error_type", "message"),
    [
        pytest.param(
            lambda tmp: ExchangeOnlyCredential(),
            ConfigError,
            "ExchangeOnlyCredential does not provide client authentication",
            id="no-client-authentication",
        ),
        pytest.param(
            lambda tmp: web_identity(tmp),
            WebIdentityConfigurationError,
            "WebIdentity client authentication requires client_id",
            id="web-identity-row-8-before-any-request",
        ),
    ],
)
async def test_require_client_authenticator_refuses(tmp_path, make, error_type, message):
    with pytest.raises(error_type, match=exactly(message)) as exc:
        require_client_authenticator(make(tmp_path))

    assert isinstance(exc.value, ConfigError)


# ClientSecret


@pytest.mark.parametrize(
    ("credentials", "expected"),
    [
        pytest.param(("cid", "secret"), basic("cid", "secret"), id="row-7-single-zone"),
        pytest.param(
            {
                ISSUER: ("zone1-id", "zone1-secret"),
                "https://zone2.keycard.cloud": ("zone2-id", "zone2-secret"),
            },
            basic("zone1-id", "zone1-secret"),
            id="row-7-configured-zone",
        ),
        pytest.param(
            ("ua:client", "a b+%41"),
            basic("ua%3Aclient", "a%20b%2B%2541"),
            id="row-8-encoding",
        ),
    ],
)
async def test_client_secret_basic_header(credentials, expected):
    auth = await ClientSecret(credentials).client_authentication(
        issuer=ISSUER, endpoint=DEVICE_ENDPOINT
    )

    assert auth == ClientAuthentication(headers={"Authorization": expected})


async def test_client_secret_row_4_unknown_zone_fails_closed():
    credential = ClientSecret({"https://zone2.keycard.cloud": ("id", "secret")})

    with pytest.raises(ConfigError, match=exactly(f"ClientSecret has no credentials for {ISSUER}")):
        await credential.client_authentication(issuer=ISSUER, endpoint=TOKEN_ENDPOINT)


# WebIdentity


@pytest.mark.parametrize(
    ("endpoint", "audience_config"),
    [
        pytest.param(DEVICE_ENDPOINT, None, id="row-7-device-endpoint"),
        pytest.param(TOKEN_ENDPOINT, None, id="token-endpoint"),
        # audience_config has no effect on any path today, so this row guards
        # client authentication against a future use of it.
        pytest.param(DEVICE_ENDPOINT, "https://other.example.com", id="row-9-string"),
        pytest.param(
            DEVICE_ENDPOINT, {ISSUER: "https://other.example.com"}, id="row-9-per-zone"
        ),
    ],
)
async def test_web_identity_assertion_for_the_endpoint(tmp_path, endpoint, audience_config):
    credential = web_identity(
        tmp_path, client_id="registered-client", audience_config=audience_config
    )

    auth = await credential.client_authentication(issuer=ISSUER, endpoint=endpoint)

    assert auth.headers == {}
    assert set(auth.form_fields) == {"client_assertion", "client_assertion_type"}
    assert auth.form_fields["client_assertion_type"] == JWT_BEARER
    header, claims = decode(auth.form_fields["client_assertion"])
    assert header == {"alg": "RS256", "typ": "JWT", "kid": credential.identity_manager.key_id}
    assert set(claims) == {"iss", "sub", "aud", "jti", "iat", "exp"}
    assert (claims["iss"], claims["sub"], claims["aud"]) == (
        "registered-client",
        "registered-client",
        endpoint,
    )
    assert claims["exp"] == claims["iat"] + 300
    uuid.UUID(claims["jti"])
    keys = KeySet.import_key_set(credential.identity_manager.get_public_jwks())
    assert jose_jwt.decode(auth.form_fields["client_assertion"], keys).claims == claims


async def test_web_identity_each_call_signs_a_new_assertion(tmp_path):
    credential = web_identity(tmp_path, client_id="registered-client")

    first = await credential.client_authentication(issuer=ISSUER, endpoint=TOKEN_ENDPOINT)
    second = await credential.client_authentication(issuer=ISSUER, endpoint=TOKEN_ENDPOINT)

    assert decode(first.form_fields["client_assertion"])[1]["jti"] != decode(
        second.form_fields["client_assertion"]
    )[1]["jti"]


@pytest.mark.parametrize(
    ("client_id", "endpoint", "message"),
    [
        pytest.param(
            None,
            DEVICE_ENDPOINT,
            "WebIdentity client authentication requires client_id",
            id="row-8-no-client_id",
        ),
        pytest.param(
            "",
            DEVICE_ENDPOINT,
            "WebIdentity client authentication requires client_id",
            id="row-8-empty-client_id",
        ),
        pytest.param(
            "registered-client",
            "",
            "WebIdentity client authentication requires an endpoint",
            id="empty-endpoint",
        ),
    ],
)
async def test_web_identity_client_authentication_misconfigured(
    tmp_path, client_id, endpoint, message
):
    credential = web_identity(tmp_path, client_id=client_id)
    credential.identity_manager = MagicMock(wraps=credential.identity_manager)

    with pytest.raises(WebIdentityConfigurationError, match=exactly(message)):
        await credential.client_authentication(issuer=ISSUER, endpoint=endpoint)

    credential.identity_manager.create_client_assertion.assert_not_called()


def exchange_client() -> MagicMock:
    client = MagicMock(_initialized=True)
    client._discovered_endpoints.token = TOKEN_ENDPOINT
    return client


@pytest.mark.parametrize(
    ("client_id", "auth_info", "expected_iss"),
    [
        pytest.param(
            "registered-client",
            {"resource_client_id": "context-client"},
            "context-client",
            id="row-10-auth-context-wins",
        ),
        pytest.param("registered-client", None, "registered-client", id="option"),
        pytest.param(
            "registered-client",
            {"resource_client_id": ""},
            "registered-client",
            id="empty-context-falls-back",
        ),
        pytest.param(None, {"resource_client_id": "context-client"}, "context-client", id="context"),
    ],
)
async def test_web_identity_row_10_token_exchange_client_id(
    tmp_path, client_id, auth_info, expected_iss
):
    credential = web_identity(tmp_path, client_id=client_id)

    request = await credential.prepare_token_exchange_request(
        client=exchange_client(), subject_token="st", resource="https://api", auth_info=auth_info
    )

    assert request.client_assertion is not None
    _, claims = decode(request.client_assertion)
    assert (claims["iss"], claims["sub"], claims["aud"]) == (
        expected_iss,
        expected_iss,
        TOKEN_ENDPOINT,
    )


@pytest.mark.parametrize(
    ("client_id", "auth_info"),
    [
        pytest.param(None, None, id="neither"),
        pytest.param("", {"resource_client_id": ""}, id="both-empty"),
    ],
)
async def test_web_identity_token_exchange_without_a_client_id(tmp_path, client_id, auth_info):
    credential = web_identity(tmp_path, client_id=client_id)

    with pytest.raises(WebIdentityConfigurationError, match=exactly(NO_CLIENT_ID)) as exc:
        await credential.prepare_token_exchange_request(
            client=exchange_client(), subject_token="st", resource="https://api", auth_info=auth_info
        )

    assert isinstance(exc.value, ValueError)
    assert isinstance(exc.value, ConfigError)


RESOURCE_SERVER = {"resource_server_url": "https://mcp.example.com"}


@pytest.mark.parametrize(
    ("client_id", "auth_info", "expected"),
    [
        pytest.param("registered-client", RESOURCE_SERVER, "registered-client", id="option"),
        pytest.param(
            "registered-client",
            {**RESOURCE_SERVER, "resource_client_id": "context-client"},
            "context-client",
            id="auth-context-wins",
        ),
        pytest.param(
            None,
            {**RESOURCE_SERVER, "resource_client_id": "context-client"},
            "context-client",
            id="context",
        ),
    ],
)
async def test_web_identity_set_client_config_client_id(tmp_path, client_id, auth_info, expected):
    credential = web_identity(tmp_path, client_id=client_id)

    config = credential.set_client_config(ClientConfig(), auth_info)

    assert config.client_id == expected


async def test_web_identity_set_client_config_without_a_client_id(tmp_path):
    credential = web_identity(tmp_path)

    with pytest.raises(WebIdentityConfigurationError, match=exactly(NO_CLIENT_ID)):
        credential.set_client_config(ClientConfig(), RESOURCE_SERVER)


# WorkloadIdentity


@pytest.mark.parametrize(
    ("client_id", "expected"),
    [
        pytest.param(
            None,
            {"client_assertion": "platform-token", "client_assertion_type": JWT_BEARER},
            id="row-4b-without-client_id",
        ),
        pytest.param(
            "app-credential-id",
            {
                "client_assertion": "platform-token",
                "client_assertion_type": JWT_BEARER,
                "client_id": "app-credential-id",
            },
            id="row-4b-with-client_id",
        ),
    ],
)
async def test_workload_identity_rows_4b_and_4c_form_fields(client_id, expected):
    # "platform-token" is not a JWT. Row 4c: it goes out unchanged.
    credential = WorkloadIdentity(lambda: "platform-token", client_id=client_id)

    auth = await credential.client_authentication(issuer=ISSUER, endpoint=TOKEN_ENDPOINT)

    assert auth == ClientAuthentication(form_fields=expected)


async def test_workload_identity_calls_the_source_on_every_request():
    tokens = iter(["token-1", "token-2"])
    credential = WorkloadIdentity(lambda: next(tokens))

    first = await credential.client_authentication(issuer=ISSUER, endpoint=TOKEN_ENDPOINT)
    second = await credential.client_authentication(issuer=ISSUER, endpoint=TOKEN_ENDPOINT)

    assert [first.form_fields["client_assertion"], second.form_fields["client_assertion"]] == [
        "token-1",
        "token-2",
    ]


def failing_source():
    raise OSError("metadata server down")


@pytest.mark.parametrize(
    ("source", "message", "cause_type"),
    [
        pytest.param(failing_source, "Error fetching identity token", OSError, id="source-raises"),
        pytest.param(
            lambda: "  ", "Identity token source returned an empty token", type(None), id="empty-token"
        ),
    ],
)
async def test_workload_identity_source_failure(source, message, cause_type):
    credential = WorkloadIdentity(source)

    with pytest.raises(WorkloadIdentityRuntimeError, match=exactly(message)) as exc:
        await credential.client_authentication(issuer=ISSUER, endpoint=TOKEN_ENDPOINT)

    assert exc.value.source == "custom"
    assert type(exc.value.__cause__) is cause_type
