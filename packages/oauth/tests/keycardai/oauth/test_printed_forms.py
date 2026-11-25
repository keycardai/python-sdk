import json
from dataclasses import asdict

from keycardai.oauth import AsyncClient, Client, ClientConfig
from keycardai.oauth.http.auth import BasicAuth, MultiZoneBasicAuth
from keycardai.oauth.server._cache import JWKSCache
from keycardai.oauth.server.agent_grant import KeycardIdentity
from keycardai.oauth.server.credentials import (
    ClientSecret,
    WebIdentity,
    WorkloadIdentity,
)
from keycardai.oauth.server.private_key import KeyPairInfo
from keycardai.oauth.server.verifier import AccessToken
from keycardai.oauth.types.models import PKCE, TokenExchangeRequest, TokenResponse


def test_row_27_token_exchange_printed_forms_and_response_json():
    request = TokenExchangeRequest(
        subject_token="subject-secret",
        actor_token="actor-secret",
        client_assertion="assertion-secret",
        resource="https://service.example",
        scope="read",
    )
    for printed in (repr(request), str(request)):
        assert "https://service.example" in printed
        assert "scope='read'" in printed
        for secret in ("subject-secret", "actor-secret", "assertion-secret"):
            assert secret not in printed

    tokens = {
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
        "id_token": "id-secret",
    }
    response = TokenResponse(**tokens, expires_in=300, scope=["read"], raw=tokens)
    for printed in (repr(response), str(response)):
        assert "expires_in=300" in printed
        assert "scope=['read']" in printed
        assert "token_type=" in printed
        for secret in tokens.values():
            assert secret not in printed
    persisted = json.loads(json.dumps(asdict(response)))
    assert {key: persisted[key] for key in tokens} == tokens
    assert persisted["raw"] == tokens


def test_printed_forms_for_other_oauth_secret_holders(tmp_path):
    issuer = "https://zone.example"
    secret = "not-for-display"

    class Source:
        def __repr__(self):
            return secret

        def identity_token(self):
            return secret

    cache = JWKSCache()
    cache.set_key("public-key-id", secret, "RS256")
    config = ClientConfig(custom_headers={"Authorization": secret})
    identity = WebIdentity(storage_dir=str(tmp_path), key_id="public-key-id")
    for value, visible in (
        (BasicAuth("public-client", secret), "public-client"),
        (MultiZoneBasicAuth({issuer: ("public-client", secret)}), issuer),
        (cache, "public-key-id"),
        (ClientSecret(("public-client", secret)), "public-client"),
        (ClientSecret({issuer: ("public-client", secret)}), issuer),
        (identity, "public-key-id"),
        (identity.identity_manager, "public-key-id"),
        (WorkloadIdentity(Source(), client_id="public-client"), "source=Source"),
        (KeycardIdentity(subject_token=secret, user_identifier="public-user"), "public-user"),
        (AccessToken(token=secret, client_id="public-client", scopes=[]), "public-client"),
        (KeyPairInfo(key_id="key-id", private_key_pem=secret, public_key_jwk={}, created_at=0), "key-id"),
        (PKCE(code_verifier=secret, code_challenge="public-challenge"), "public-challenge"),
        (Client(issuer, config=config), issuer),
        (AsyncClient(issuer, config=config), issuer),
    ):
        for printed in (repr(value), str(value)):
            assert visible in printed
            assert secret not in printed
            assert "BEGIN PRIVATE KEY" not in printed
