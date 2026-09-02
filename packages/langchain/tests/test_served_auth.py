"""Inbound authentication and ownership for a served agent.

Hermetic: bearer verification goes through the injectable seam, so no zone and
no network are involved. Authorization events run through the server's own
handler resolution, user normalization and owner filter matcher, so the
assertions reflect what a deployment does rather than a local
reimplementation.
"""

from __future__ import annotations

import os

import pytest
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph_sdk import Auth
from langgraph_sdk.auth.types import StudioUser
from starlette.exceptions import HTTPException

from keycardai.langchain import (
    KeycardGrantMiddleware,
    KeycardIdentity,
    caller_from_config,
    get_access_context,
)
from keycardai.langchain.auth import (
    VerifiedCaller,
    install_owner_authorization,
    zone_authenticator,
)
from keycardai.oauth import TokenResponse
from keycardai.oauth.types.models import TokenExchangeRequest

# langgraph_api reads these at import time; nothing here connects anywhere.
os.environ.setdefault("REDIS_URI", "redis://localhost:6379")
os.environ.setdefault("DATABASE_URI", "postgres://localhost/none")
os.environ.setdefault("POSTGRES_URI", "postgres://localhost/none")

custom = pytest.importorskip("langgraph_api.auth.custom")
inmem_ops = pytest.importorskip("langgraph_runtime_inmem.ops")

ZONE = "https://zone.example.test"
RESOURCE = "https://agent.example.test"
METADATA_URL = f"{ZONE}/.well-known/oauth-authorization-server"
ADA = "ada@example.test"
GRACE = "grace@example.test"
CALLERS = {"token-a": ADA, "token-b": GRACE}
PROMPT = {"messages": [HumanMessage("Read the delegated token.")]}


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


@pytest.fixture
def dispatch(monkeypatch):
    """Run one authorization event the way the server runs it."""
    auth = install_owner_authorization(Auth())
    monkeypatch.setattr(custom, "get_auth_instance", lambda: auth)

    async def _dispatch(user, resource: str, action: str, value: dict):
        ctx = Auth.types.AuthContext(
            user=user, permissions=[], resource=resource, action=action
        )
        return await custom.handle_event(ctx, value)

    return _dispatch


def user(identity: str):
    return custom.normalize_user({"identity": identity})


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


async def test_thread_creation_stamps_the_owner(dispatch) -> None:
    value = {"thread_id": "t-1", "metadata": None}
    filters = await dispatch(user(ADA), "threads", "create", value)
    assert value["metadata"] == {"owner": ADA}
    assert filters == {"owner": ADA}


async def test_body_supplied_owner_cannot_be_forged(dispatch) -> None:
    value = {"thread_id": "t-1", "metadata": {"owner": GRACE}}
    filters = await dispatch(user(ADA), "threads", "create", value)
    assert value["metadata"]["owner"] == ADA
    assert filters == {"owner": ADA}


async def test_thread_update_stamps_the_owner(dispatch) -> None:
    thread = {"thread_id": "t-1", "metadata": {}}
    await dispatch(user(ADA), "threads", "create", thread)
    # The body names another identity; the stamp must overwrite it before the
    # server merges the update into the thread's metadata.
    update = {"thread_id": "t-1", "metadata": {"owner": GRACE}}
    filters = await dispatch(user(ADA), "threads", "update", update)
    assert update["metadata"]["owner"] == ADA
    assert filters == {"owner": ADA}
    thread["metadata"].update(update["metadata"])
    assert inmem_ops._check_filter_match(thread["metadata"], {"owner": ADA})
    assert not inmem_ops._check_filter_match(thread["metadata"], {"owner": GRACE})


async def test_cross_owner_thread_read_is_filtered(dispatch) -> None:
    thread = {"thread_id": "t-1", "metadata": {}}
    await dispatch(user(ADA), "threads", "create", thread)
    own = await dispatch(user(ADA), "threads", "read", {"thread_id": "t-1"})
    other = await dispatch(user(GRACE), "threads", "read", {"thread_id": "t-1"})
    assert inmem_ops._check_filter_match(thread["metadata"], own)
    assert not inmem_ops._check_filter_match(thread["metadata"], other)


async def test_cross_owner_resume_is_filtered(dispatch) -> None:
    thread = {"thread_id": "t-1", "metadata": {}}
    await dispatch(user(ADA), "threads", "create", thread)
    resume = {"thread_id": "t-1", "assistant_id": "agent", "metadata": {}}
    filters = await dispatch(user(GRACE), "threads", "create_run", resume)
    assert not inmem_ops._check_filter_match(thread["metadata"], filters)
    assert resume["metadata"] == {"owner": GRACE}


async def test_store_items_are_scoped_to_the_caller(dispatch) -> None:
    put = {"namespace": ("memories",), "key": "k", "value": {}}
    await dispatch(user(ADA), "store", "put", put)
    segment = put["namespace"][0]
    assert put["namespace"] == (segment, "memories")
    # The store rejects namespace labels containing periods, so the owner
    # segment must be store-safe, never the raw email identity.
    assert "." not in segment
    assert segment != ADA
    search = {"namespace_prefix": ("memories",)}
    await dispatch(user(ADA), "store", "search", search)
    assert search["namespace_prefix"] == (segment, "memories")


async def test_store_owner_segments_are_distinct_per_caller(dispatch) -> None:
    ada_put = {"namespace": ("memories",), "key": "k", "value": {}}
    grace_put = {"namespace": ("memories",), "key": "k", "value": {}}
    await dispatch(user(ADA), "store", "put", ada_put)
    await dispatch(user(GRACE), "store", "put", grace_put)
    assert ada_put["namespace"][0] != grace_put["namespace"][0]


async def test_prefixless_namespace_listing_is_scoped_to_the_caller(dispatch) -> None:
    """A list_namespaces call with no prefix must not enumerate other owners."""
    listing: dict = {}
    await dispatch(user(ADA), "store", "list_namespaces", listing)
    scoped = listing.get("namespace") or listing.get("namespace_prefix")
    assert scoped is not None
    assert len(scoped) == 1
    assert "." not in scoped[0]


async def test_unmatched_resource_action_pairs_are_denied(dispatch) -> None:
    """Authorization fails open without a catch-all handler."""
    for resource, action in (
        ("crons", "create"),
        ("assistants", "create"),
        ("assistants", "update"),
        ("assistants", "delete"),
    ):
        with pytest.raises(HTTPException) as caught:
            await dispatch(user(ADA), resource, action, {})
        assert caught.value.status_code == 403


async def test_assistant_reads_stay_open_to_authenticated_callers(dispatch) -> None:
    assert (
        await dispatch(user(ADA), "assistants", "read", {"assistant_id": "a"}) is None
    )
    assert await dispatch(user(ADA), "assistants", "search", {}) is None


async def test_studio_user_is_denied(dispatch) -> None:
    studio = StudioUser("langgraph-studio-user")
    for resource, action in (
        ("threads", "create"),
        ("threads", "read"),
        ("store", "put"),
    ):
        with pytest.raises(HTTPException) as caught:
            await dispatch(studio, resource, action, {"namespace": ("x",)})
        assert caught.value.status_code == 403


class StubExchangeClient:
    """Stands in for keycardai.oauth.AsyncClient on the exchange path."""

    def __init__(self) -> None:
        self.exchange_calls: list[TokenExchangeRequest] = []

    async def exchange_token(self, request: TokenExchangeRequest) -> TokenResponse:
        self.exchange_calls.append(request)
        return TokenResponse(
            access_token=f"obo-{request.subject_token}", token_type="Bearer"
        )


@tool
def read_delegated_token() -> str:
    """Read the delegated Keycard token for the agent's resource."""
    access = get_access_context()
    if access.has_error():
        return f"GLOBAL_ERROR: {access.get_error()}"
    return f"TOKEN: {access.access(RESOURCE).access_token}"


class _ToolBindableFakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ANN201
        return self


def served_agent(stub: StubExchangeClient):
    middleware = KeycardGrantMiddleware(
        resources=[RESOURCE], client=stub, identity_source="auth_user"
    )
    model = _ToolBindableFakeModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_delegated_token",
                            "args": {},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
    )
    return create_agent(
        model=model,
        tools=[read_delegated_token],
        middleware=[middleware],
        context_schema=KeycardIdentity,
    )


def last_tool_message(result: dict) -> ToolMessage:
    messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert messages, f"no ToolMessage in {result['messages']}"
    return messages[-1]


async def run_config(authorization: str) -> dict:
    """The run config a server builds from an authenticated request."""
    hook = authenticator()
    served = custom.normalize_user(await hook(headers=headers(authorization)))
    return {"configurable": {"langgraph_auth_user": served}}


async def test_auth_user_mode_grants_under_the_verified_caller() -> None:
    stub = StubExchangeClient()
    config = await run_config("Bearer token-a")
    result = await served_agent(stub).ainvoke(PROMPT, config)
    assert "TOKEN: obo-token-a" in last_tool_message(result).content
    assert stub.exchange_calls[0].subject_token == "token-a"
    assert caller_from_config(config).identity == ADA


async def test_auth_user_mode_keeps_two_callers_apart() -> None:
    """The single-slot token store this mode replaces made the last caller win."""
    tokens = []
    for authorization in ("Bearer token-a", "Bearer token-b"):
        stub = StubExchangeClient()
        await served_agent(stub).ainvoke(PROMPT, await run_config(authorization))
        tokens.append(stub.exchange_calls[0].subject_token)
    assert tokens == ["token-a", "token-b"]


async def test_auth_user_mode_ignores_caller_supplied_context() -> None:
    """Runtime context comes from the request body, so it cannot name an identity."""
    stub = StubExchangeClient()
    result = await served_agent(stub).ainvoke(
        PROMPT, context=KeycardIdentity(subject_token="forged-token", as_self=True)
    )
    assert "GLOBAL_ERROR" in last_tool_message(result).content
    assert "missing_identity" in last_tool_message(result).content
    assert not stub.exchange_calls


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
