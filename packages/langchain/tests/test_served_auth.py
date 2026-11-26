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


async def test_cross_owner_thread_update_is_filtered(dispatch) -> None:
    """The specific update handler replaces the generic threads filter for
    its action, so it must return the owner filter itself: a stamp-only
    handler would let an attacker update, and thereby steal, other callers'
    threads."""
    thread = {"thread_id": "t-1", "metadata": {}}
    await dispatch(user(ADA), "threads", "create", thread)
    update = {"thread_id": "t-1", "metadata": {"note": "mine now"}}
    filters = await dispatch(user(GRACE), "threads", "update", update)
    assert not inmem_ops._check_filter_match(thread["metadata"], filters)
    # The stamp still ran, so even the attempt carries the attacker's own
    # identity, never a body-supplied one.
    assert update["metadata"]["owner"] == GRACE


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
