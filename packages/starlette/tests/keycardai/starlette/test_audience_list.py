"""Conformance tests for the bearer-token-verification-middleware spec."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from joserfc import jwt
from joserfc.jwk import import_key
from starlette.testclient import TestClient

from keycardai.oauth.server.exceptions import AuthProviderConfigurationError
from keycardai.starlette import AuthProvider

ISSUER = "https://zone.example.com"


@pytest.fixture(scope="module")
def signing_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return import_key(pem, "RSA")


@pytest.mark.parametrize(
    ("aud", "status"),
    [
        pytest.param("resource-b", 200, id="test_row_8a_scalar_match"),
        pytest.param("resource-c", 401, id="test_row_8b_scalar_mismatch"),
        pytest.param(["resource-c", "resource-b"], 200, id="test_row_8c_array_match"),
        pytest.param(["resource-c", "resource-d"], 401, id="test_row_8d_array_mismatch"),
    ],
)
def test_middleware_audience_intersection(monkeypatch, signing_key, aud, status):
    kid = "audience-key"
    jwk = signing_key.as_dict(private=False) | {"kid": kid, "alg": "RS256"}
    monkeypatch.setattr(
        "keycardai.oauth.server.verifier.fetch_jwks", AsyncMock(return_value=[jwk])
    )
    factory = MagicMock()
    factory.create_client.return_value.discover_server_metadata.return_value.jwks_uri = (
        f"{ISSUER}/jwks"
    )
    provider = AuthProvider(
        zone_url=ISSUER, audience=["resource-a", "resource-b"], client_factory=factory
    )
    app = FastAPI()
    provider.install(app)

    @app.get("/resource")
    async def resource():
        return {"ok": True}

    now = int(time.time())
    token = jwt.encode(
        {"alg": "RS256", "kid": kid},
        {
            "iss": ISSUER,
            "sub": "user",
            "aud": aud,
            "client_id": "client",
            "iat": now,
            "exp": now + 60,
        },
        signing_key,
    )
    with TestClient(app) as client:
        response = client.get("/resource", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == status
    if status == 401:
        assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]


def test_row_8e_empty_audiences_are_a_provider_configuration_error():
    with pytest.raises(AuthProviderConfigurationError, match="audience"):
        AuthProvider(zone_url=ISSUER, audience=[])
