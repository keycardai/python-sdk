"""Authentication and ownership unit tests using only the LangGraph SDK."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langgraph_sdk import Auth
from starlette.exceptions import HTTPException

from keycardai.langchain import (
    KeycardGrantMiddleware,
    KeycardIdentity,
    caller_from_config,
)
from keycardai.langchain.auth import (
    VerifiedCaller,
    install_owner_authorization,
    zone_authenticator,
)

ZONE = "https://zone.example.test"
RESOURCE = "https://agent.example.test"
METADATA_URL = f"{ZONE}/.well-known/oauth-authorization-server"
ADA = "ada@example.test"
GRACE = "grace@example.test"
CALLERS = {"token-a": ADA, "token-b": GRACE}


async def stub_verify(token: str) -> VerifiedCaller:
    if token not in CALLERS:
        raise ValueError("token is not zone-issued")
    return VerifiedCaller(identity=CALLERS[token], scopes=("openid", "email"))


def authenticator():
    return zone_authenticator(zone_url=ZONE, resource=RESOURCE, verify=stub_verify)


def headers(authorization: str | None) -> dict[bytes, bytes]:
    if authorization is None:
        return {b"content-type": b"application/json"}
    return {b"authorization": authorization.encode()}


async def test_valid_bearer_yields_identity_and_raw_token() -> None:
    result = await authenticator()(headers=headers("Bearer token-a"))
    assert result["identity"] == ADA
    assert result["display_name"] == ADA
    assert result["subject_token"] == "token-a"
    assert result["permissions"] == ["openid", "email"]


async def test_missing_bearer_is_challenged() -> None:
    with pytest.raises(HTTPException) as caught:
        await authenticator()(headers=headers(None))
    challenge = caught.value.headers["WWW-Authenticate"]
    assert caught.value.status_code == 401
    assert challenge.startswith("Bearer ")
    assert 'error="invalid_request"' in challenge
    assert f'authorization_uri="{METADATA_URL}"' in challenge


async def test_invalid_bearer_is_challenged() -> None:
    with pytest.raises(HTTPException) as caught:
        await authenticator()(headers=headers("Bearer token-forged"))
    challenge = caught.value.headers["WWW-Authenticate"]
    assert caught.value.status_code == 401
    assert 'error="invalid_token"' in challenge
    assert METADATA_URL in challenge


async def test_non_bearer_authorization_is_challenged() -> None:
    with pytest.raises(HTTPException) as caught:
        await authenticator()(headers=headers("Basic YWRhOm9wZW4="))
    assert caught.value.status_code == 401


async def test_rejection_is_the_starlette_exception_that_keeps_headers() -> None:
    """The SDK exception drops response headers and coerces statuses."""
    with pytest.raises(HTTPException) as caught:
        await authenticator()(headers=headers("Bearer token-forged"))
    assert not isinstance(caught.value, Auth.exceptions.HTTPException)


async def test_an_exploding_verifier_is_a_challenge_not_a_500() -> None:
    async def explode(token: str) -> VerifiedCaller:
        raise MemoryError("the verifier fell over")

    hook = zone_authenticator(zone_url=ZONE, resource=RESOURCE, verify=explode)
    with pytest.raises(HTTPException) as caught:
        await hook(headers=headers("Bearer token-a"))
    assert caught.value.status_code == 401
    assert "MemoryError" in caught.value.headers["WWW-Authenticate"]


class FakeAccessToken:
    def __init__(self, scopes: tuple[str, ...]) -> None:
        self.scopes = list(scopes)


def install_fake_zone_verifier(monkeypatch, claims: dict, scopes=("email",)):
    """Stand in for the real JWKS verifier, so the default path stays hermetic."""
    from keycardai.oauth.server import verifier as verifier_module
    from keycardai.oauth.utils import jwt as jwt_module

    built: list[dict] = []

    class FakeTokenVerifier:
        def __init__(self, *, issuer: str, audience: str) -> None:
            built.append({"issuer": issuer, "audience": audience})

        async def verify_token(self, token: str) -> FakeAccessToken:
            if token != "token-a":
                raise ValueError("signature does not verify")
            return FakeAccessToken(scopes)

    monkeypatch.setattr(verifier_module, "TokenVerifier", FakeTokenVerifier)
    monkeypatch.setattr(jwt_module, "get_claims", lambda token: claims)
    return built


async def test_default_path_verifies_against_the_zone_at_this_resource(
    monkeypatch,
) -> None:
    built = install_fake_zone_verifier(monkeypatch, {"email": ADA, "sub": "abc"})
    hook = zone_authenticator(zone_url=ZONE, resource=RESOURCE)
    # The verifier is built on first use, so importing an auth module never
    # reaches the zone.
    assert built == []
    result = await hook(headers=headers("Bearer token-a"))
    assert built == [{"issuer": ZONE, "audience": RESOURCE}]
    assert result["identity"] == ADA
    assert result["permissions"] == ["email"]


async def test_trailing_slash_zone_url_still_verifies(monkeypatch) -> None:
    """Zone tokens carry no trailing slash in their issuer, and the verifier
    matches issuers exactly, so the configured slash must never reach it."""
    built = install_fake_zone_verifier(monkeypatch, {"email": ADA, "sub": "abc"})
    hook = zone_authenticator(zone_url=f"{ZONE}/", resource=RESOURCE)
    result = await hook(headers=headers("Bearer token-a"))
    assert built == [{"issuer": ZONE, "audience": RESOURCE}]
    assert result["identity"] == ADA


async def test_identity_falls_back_to_the_subject_claim(monkeypatch) -> None:
    install_fake_zone_verifier(monkeypatch, {"sub": "user-abc"})
    hook = zone_authenticator(zone_url=ZONE, resource=RESOURCE)
    result = await hook(headers=headers("Bearer token-a"))
    assert result["identity"] == "user-abc"


async def test_a_token_with_no_usable_identity_claim_is_challenged(monkeypatch) -> None:
    install_fake_zone_verifier(monkeypatch, {"iss": ZONE})
    hook = zone_authenticator(zone_url=ZONE, resource=RESOURCE)
    with pytest.raises(HTTPException) as caught:
        await hook(headers=headers("Bearer token-a"))
    assert caught.value.status_code == 401


async def test_a_token_the_zone_rejects_is_challenged(monkeypatch) -> None:
    install_fake_zone_verifier(monkeypatch, {"email": ADA})
    hook = zone_authenticator(zone_url=ZONE, resource=RESOURCE)
    with pytest.raises(HTTPException) as caught:
        await hook(headers=headers("Bearer token-forged"))
    assert caught.value.status_code == 401


def test_zone_and_resource_are_required() -> None:
    with pytest.raises(ValueError):
        zone_authenticator(zone_url="", resource=RESOURCE)
    with pytest.raises(ValueError):
        zone_authenticator(zone_url=ZONE, resource="")


@pytest.fixture
def owner_auth() -> Auth:
    return install_owner_authorization(Auth())


def owner_context(resource, action, identity=ADA) -> Auth.types.AuthContext:
    return Auth.types.AuthContext(
        user=SimpleNamespace(identity=identity),
        permissions=[],
        resource=resource,
        action=action,
    )


@pytest.mark.parametrize("action", ["create", "create_run", "update"])
async def test_thread_write_handler_sets_missing_metadata(owner_auth, action) -> None:
    (handler,) = owner_auth._handlers["threads", action]
    value = {"thread_id": "t-1"}

    assert await handler(owner_context("threads", action), value) == {"owner": ADA}
    assert value == {"thread_id": "t-1", "metadata": {"owner": ADA}}


async def test_thread_handler_uses_only_the_authenticated_identity(owner_auth) -> None:
    (handler,) = owner_auth._handlers["threads", "*"]
    value = {"metadata": {"owner": GRACE}}

    assert await handler(owner_context("threads", "search"), value) == {"owner": ADA}
    assert value == {"metadata": {"owner": GRACE}}


async def test_thread_handler_requires_a_nonempty_identity(owner_auth) -> None:
    (handler,) = owner_auth._handlers["threads", "*"]

    with pytest.raises(HTTPException, match="Authenticated identity is required") as err:
        await handler(owner_context("threads", "read", identity=""), {})
    assert err.value.status_code == 403


async def test_store_handler_scopes_namespace_and_prefix(owner_auth) -> None:
    (handler,) = owner_auth._handlers["store", "*"]
    value = {"namespace": ("memory",), "namespace_prefix": ("recent",), "limit": 5}

    assert await handler(owner_context("store", "search"), value) is None
    segment = value["namespace"][0]
    assert value == {
        "namespace": (segment, "memory"),
        "namespace_prefix": (segment, "recent"),
        "limit": 5,
    }


@pytest.mark.parametrize("action", ["read", "search"])
async def test_assistant_handler_checks_identity_without_adding_a_filter(
    owner_auth, action
) -> None:
    (handler,) = owner_auth._handlers["assistants", action]
    assert await handler(owner_context("assistants", action), {}) is None

    with pytest.raises(HTTPException, match="Authenticated identity is required"):
        await handler(owner_context("assistants", action, identity=""), {})


async def test_global_handler_denies_without_modifying_input(owner_auth) -> None:
    (handler,) = owner_auth._global_handlers
    value = {"metadata": {"owner": ADA}}

    assert await handler(owner_context("crons", "create"), value) is False
    assert value == {"metadata": {"owner": ADA}}


async def test_caller_from_config_reads_verified_user_dict() -> None:
    user = await authenticator()(headers=headers("Bearer token-a"))
    caller = caller_from_config({"configurable": {"langgraph_auth_user": user}})
    assert caller is not None
    assert caller.identity == ADA
    assert caller.subject_token == "token-a"


def test_auth_user_mode_rejects_a_second_identity_source() -> None:
    with pytest.raises(ValueError):
        KeycardGrantMiddleware(
            zone_url=ZONE,
            resources=[RESOURCE],
            identity_source="auth_user",
            fallback_identity=KeycardIdentity(as_self=True),
        )


def test_unknown_identity_source_is_rejected() -> None:
    with pytest.raises(ValueError):
        KeycardGrantMiddleware(
            zone_url=ZONE, resources=[RESOURCE], identity_source="whatever"
        )


def test_caller_from_config_needs_both_identity_and_token() -> None:
    assert caller_from_config(None) is None
    assert caller_from_config({"configurable": {}}) is None
    assert caller_from_config({"configurable": {"langgraph_auth_user": {}}}) is None
    partial = {"configurable": {"langgraph_auth_user": {"identity": ADA}}}
    assert caller_from_config(partial) is None
