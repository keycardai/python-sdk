"""Authorization endpoint query parameters survive both public URL-building paths."""

from urllib.parse import parse_qs, urlsplit

import pytest

from keycardai.oauth import build_authorize_url
from keycardai.oauth.pkce import begin_authorization
from keycardai.oauth.types.models import AuthorizationServerMetadata
from keycardai.oauth.utils.pkce import PKCEChallenge, PKCEGenerator


@pytest.mark.parametrize(
    "existing_query",
    ["tenant=acme", "tenant=acme&redirect=%2Fteam%3Fa%3Db&empty=", ""],
)
def test_preserves_endpoint_query(existing_query):
    url = build_authorize_url(
        f"https://auth.example.com/authorize?{existing_query}",
        client_id="my-client",
        redirect_uri="http://localhost:9999/callback",
        pkce=PKCEChallenge(
            code_verifier="test_verifier",
            code_challenge="test_challenge",
            code_challenge_method="S256",
        ),
        resources=["https://api.example.com", "https://files.example.com"],
        scope="openid profile",
        state="csrf-token",
    )
    parsed = urlsplit(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    assert parsed.path == "/authorize"
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["my-client"]
    assert qs["state"] == ["csrf-token"]
    assert qs["code_challenge"] == ["test_challenge"]
    assert qs["scope"] == ["openid profile"]
    assert qs["resource"] == [
        "https://api.example.com",
        "https://files.example.com",
    ]
    if existing_query:
        assert parsed.query.startswith(f"{existing_query}&")
        assert qs["tenant"] == ["acme"]
    if "empty=" in existing_query:
        assert qs["empty"] == [""]
        assert qs["redirect"] == ["/team?a=b"]


@pytest.mark.asyncio
async def test_begin_preserves_query_from_discovered_authorization_endpoint():
    metadata = AuthorizationServerMetadata(
        issuer="https://auth.example.com",
        authorization_endpoint="https://auth.example.com/authorize?tenant=acme",
        token_endpoint="https://auth.example.com/token",
    )
    result = await begin_authorization(
        client_id="my-app",
        redirect_uri="https://app.example.com/callback",
        metadata=metadata,
        scopes=["openid", "profile"],
    )
    params = parse_qs(urlsplit(result.url).query)
    assert params["tenant"] == ["acme"]
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["my-app"]
    assert params["scope"] == ["openid profile"]
    assert params["state"] == [result.state]
    assert params["code_challenge"] == [
        PKCEGenerator.generate_code_challenge(result.code_verifier)
    ]
