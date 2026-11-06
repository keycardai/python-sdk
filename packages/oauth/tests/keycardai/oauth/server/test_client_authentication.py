"""Client authentication on ClientSecret, WebIdentity, and WorkloadIdentity.

Test names name the credential spec and row they cover
(specs/application-credentials/*.md in the Keycard SDK spec).
"""

import base64
import json
from unittest.mock import MagicMock

import pytest
from joserfc import jwt as jose_jwt
from joserfc.jwk import KeySet

from keycardai.oauth import ClientAuthenticator, ClientConfig, ConfigError
from keycardai.oauth.server.credentials import (
    ClientSecret,
    WebIdentity,
    WorkloadIdentity,
)

ISSUER = "https://zone1.keycard.cloud"
DEVICE_ENDPOINT = "https://zone1.keycard.cloud/oauth/device_authorization"
TOKEN_ENDPOINT = "https://zone1.keycard.cloud/oauth/token"
JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

pytestmark = pytest.mark.asyncio


def basic(client_id: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()


def web_identity(tmp_path, **kwargs) -> WebIdentity:
    return WebIdentity(server_name="server", storage_dir=str(tmp_path), **kwargs)


def decode(assertion: str) -> tuple[dict, dict]:
    header, payload, _ = assertion.split(".")

    def part(segment: str) -> dict:
        return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))

    return part(header), part(payload)


async def test_credentials_declare_client_authentication(tmp_path):
    credentials = [
        ClientSecret(("cid", "secret")),
        web_identity(tmp_path),
        WorkloadIdentity(lambda: "token"),
    ]
    assert all(isinstance(c, ClientAuthenticator) for c in credentials)


@pytest.mark.parametrize(
    ("credentials", "expected"),
    [
        pytest.param(("cid", "secret"), basic("cid", "secret"), id="single-zone"),
        pytest.param(
            {
                ISSUER: ("zone1-id", "zone1-secret"),
                "https://zone2.keycard.cloud": ("zone2-id", "zone2-secret"),
            },
            basic("zone1-id", "zone1-secret"),
            id="configured-zone",
        ),
    ],
)
async def test_client_secret_row_7_basic_header(credentials, expected):
    auth = await ClientSecret(credentials).client_authentication(
        issuer=ISSUER, endpoint=DEVICE_ENDPOINT
    )

    assert auth.headers == {"Authorization": expected}
    assert auth.form_fields == {}


async def test_client_secret_unknown_zone_fails_closed():
    credential = ClientSecret({"https://zone2.keycard.cloud": ("id", "secret")})

    with pytest.raises(KeyError, match="not configured"):
        await credential.client_authentication(issuer=ISSUER, endpoint=TOKEN_ENDPOINT)


@pytest.mark.parametrize(
    "audience_config",
    [
        pytest.param(None, id="row-7"),
        pytest.param("https://other-audience.example.com", id="row-9-audience_config"),
    ],
)
async def test_web_identity_rows_7_and_9_assertion_for_the_endpoint(
    tmp_path, audience_config
):
    credential = web_identity(
        tmp_path, client_id="registered-client", audience_config=audience_config
    )

    auth = await credential.client_authentication(
        issuer=ISSUER, endpoint=DEVICE_ENDPOINT
    )

    assert auth.headers == {}
    assert set(auth.form_fields) == {"client_assertion", "client_assertion_type"}
    assert auth.form_fields["client_assertion_type"] == JWT_BEARER
    header, claims = decode(auth.form_fields["client_assertion"])
    assert header["alg"] == "RS256"
    assert header["kid"] == credential.identity_manager.key_id
    assert claims["iss"] == claims["sub"] == "registered-client"
    assert claims["aud"] == DEVICE_ENDPOINT
    assert claims["exp"] == claims["iat"] + 300
    assert claims["jti"]


async def test_web_identity_assertion_verifies_with_its_public_key(tmp_path):
    credential = web_identity(tmp_path, client_id="registered-client")

    auth = await credential.client_authentication(issuer=ISSUER, endpoint=TOKEN_ENDPOINT)

    keys = KeySet.import_key_set(credential.identity_manager.get_public_jwks())
    token = jose_jwt.decode(auth.form_fields["client_assertion"], keys)
    assert token.claims["aud"] == TOKEN_ENDPOINT


async def test_web_identity_each_call_signs_a_new_assertion(tmp_path):
    credential = web_identity(tmp_path, client_id="registered-client")

    first = await credential.client_authentication(issuer=ISSUER, endpoint=TOKEN_ENDPOINT)
    second = await credential.client_authentication(issuer=ISSUER, endpoint=TOKEN_ENDPOINT)

    assert decode(first.form_fields["client_assertion"])[1]["jti"] != decode(
        second.form_fields["client_assertion"]
    )[1]["jti"]


async def test_web_identity_row_8_without_client_id(tmp_path):
    credential = web_identity(tmp_path)
    credential.identity_manager = MagicMock(wraps=credential.identity_manager)

    with pytest.raises(ConfigError, match="requires client_id"):
        await credential.client_authentication(issuer=ISSUER, endpoint=DEVICE_ENDPOINT)

    credential.identity_manager.create_client_assertion.assert_not_called()


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
        pytest.param(None, {"resource_client_id": "context-client"}, "context-client", id="context"),
    ],
)
async def test_web_identity_row_10_token_exchange_client_id(
    tmp_path, client_id, auth_info, expected_iss
):
    credential = web_identity(tmp_path, client_id=client_id)
    client = MagicMock(_initialized=True)
    client._discovered_endpoints.token = TOKEN_ENDPOINT

    request = await credential.prepare_token_exchange_request(
        client=client, subject_token="st", resource="https://api", auth_info=auth_info
    )

    assert request.client_assertion is not None
    _, claims = decode(request.client_assertion)
    assert claims["iss"] == claims["sub"] == expected_iss
    assert claims["aud"] == TOKEN_ENDPOINT


async def test_web_identity_token_exchange_without_any_client_id(tmp_path):
    credential = web_identity(tmp_path)

    with pytest.raises(ValueError, match="'resource_client_id' or the client_id option"):
        await credential.prepare_token_exchange_request(
            client=MagicMock(), subject_token="st", resource="https://api"
        )


@pytest.mark.parametrize(
    ("client_id", "auth_info", "expected"),
    [
        pytest.param(
            "registered-client",
            {"resource_server_url": "https://mcp.example.com"},
            "registered-client",
            id="option",
        ),
        pytest.param(
            "registered-client",
            {
                "resource_server_url": "https://mcp.example.com",
                "resource_client_id": "context-client",
            },
            "context-client",
            id="auth-context-wins",
        ),
    ],
)
async def test_web_identity_set_client_config_client_id(
    tmp_path, client_id, auth_info, expected
):
    credential = web_identity(tmp_path, client_id=client_id)

    config = credential.set_client_config(ClientConfig(), auth_info)

    assert config.client_id == expected


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
async def test_workload_identity_row_4b_form_fields(client_id, expected):
    credential = WorkloadIdentity(lambda: "platform-token", client_id=client_id)

    auth = await credential.client_authentication(issuer=ISSUER, endpoint=TOKEN_ENDPOINT)

    assert auth.headers == {}
    assert auth.form_fields == expected
