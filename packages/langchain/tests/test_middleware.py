"""Middleware behavior, exercised through a real create_agent loop.

The exchange client is a stub, so no zone or network is involved; everything
else (the agent graph, the middleware hooks, the tool call) is real.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time

import pytest
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from keycardai.langchain import (
    KeycardGrantMiddleware,
    KeycardIdentity,
    get_access_context,
)
from keycardai.oauth import TokenResponse
from keycardai.oauth.types.models import TokenExchangeRequest

RESOURCE = "https://api.example.test"
PROMPT = {"messages": [HumanMessage("Read the delegated token.")]}


def jwt_with_exp(exp: float) -> str:
    """An unsigned JWT carrying only exp; the middleware never verifies."""

    def b64(part: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(part).encode())
        return raw.rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64({'exp': exp})}.sig"


class StubExchangeClient:
    """Stands in for keycardai.oauth.AsyncClient on the exchange paths."""

    def __init__(self) -> None:
        self.exchange_calls: list[TokenExchangeRequest] = []
        self.impersonate_calls: list[dict[str, str]] = []
        self.self_calls: list[dict[str, str]] = []
        self.granted = True
        self.self_granted = True
        self.denied_resources: set[str] = set()

    async def exchange_token(self, request: TokenExchangeRequest) -> TokenResponse:
        self.exchange_calls.append(request)
        if not self.granted or request.resource in self.denied_resources:
            raise RuntimeError("no grant for this resource yet")
        return TokenResponse(
            access_token=f"obo-token-for-{request.resource}",
            token_type="Bearer",
            expires_in=300,
        )

    async def impersonate(
        self, *, user_identifier: str, resource: str, scope: str | None = None
    ) -> TokenResponse:
        self.impersonate_calls.append({"user": user_identifier, "resource": resource})
        return TokenResponse(
            access_token=f"impersonated-{user_identifier}-for-{resource}",
            token_type="Bearer",
        )

    async def client_credentials_grant(self, request=None, **kwargs) -> TokenResponse:
        self.self_calls.append(kwargs)
        if not self.self_granted:
            raise RuntimeError("policy denies this application self access")
        return TokenResponse(
            access_token=f"self-token-for-{kwargs.get('resource')}",
            token_type="Bearer",
        )


@tool
def read_delegated_token(resource: str) -> str:
    """Read the delegated Keycard token for a resource."""
    access = get_access_context()
    if access.has_error():
        return f"GLOBAL_ERROR: {access.get_error()}"
    if access.has_resource_error(resource):
        return f"RESOURCE_ERROR: {access.get_resource_error(resource)}"
    return f"TOKEN: {access.access(resource).access_token}"


class _ToolBindableFakeModel(GenericFakeChatModel):
    """GenericFakeChatModel that tolerates bind_tools; the script drives calls."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ANN201
        return self


def scripted_model() -> GenericFakeChatModel:
    return _ToolBindableFakeModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_delegated_token",
                            "args": {"resource": RESOURCE},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
    )


def build_agent(stub: StubExchangeClient, **middleware_kwargs) -> object:
    middleware = KeycardGrantMiddleware(
        resources=[RESOURCE], client=stub, **middleware_kwargs
    )
    checkpointer = (
        InMemorySaver()
        if middleware_kwargs.get("authorization_url")
        or middleware_kwargs.get("sign_in_url")
        else None
    )
    return create_agent(
        model=scripted_model(),
        tools=[read_delegated_token],
        middleware=[middleware],
        context_schema=KeycardIdentity,
        checkpointer=checkpointer,
    )


def last_tool_message(result: dict) -> ToolMessage:
    messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert messages, f"no ToolMessage in {result['messages']}"
    return messages[-1]


def test_on_behalf_of_exchanges_the_callers_token() -> None:
    stub = StubExchangeClient()
    result = build_agent(stub).invoke(
        PROMPT, context=KeycardIdentity(subject_token="caller-token")
    )
    assert f"TOKEN: obo-token-for-{RESOURCE}" in last_tool_message(result).content
    assert stub.exchange_calls[0].subject_token == "caller-token"


async def test_on_behalf_of_works_on_the_async_path() -> None:
    stub = StubExchangeClient()
    result = await build_agent(stub).ainvoke(
        PROMPT, context=KeycardIdentity(subject_token="caller-token")
    )
    assert f"TOKEN: obo-token-for-{RESOURCE}" in last_tool_message(result).content


def test_impersonation_uses_the_substitute_user_path() -> None:
    stub = StubExchangeClient()
    result = build_agent(stub).invoke(
        PROMPT, context=KeycardIdentity(user_identifier="user@example.com")
    )
    assert "TOKEN: impersonated-user@example.com" in last_tool_message(result).content
    assert not stub.exchange_calls
    assert len(stub.impersonate_calls) == 1


def test_missing_identity_is_recorded_not_raised() -> None:
    stub = StubExchangeClient()
    result = build_agent(stub).invoke(PROMPT)
    content = last_tool_message(result).content
    assert "GLOBAL_ERROR" in content
    assert "missing_identity" in content
    assert not stub.exchange_calls


def test_tool_schema_carries_no_keycard_plumbing() -> None:
    """The model must not see (or be able to supply) auth arguments."""
    properties = read_delegated_token.args_schema.model_json_schema()["properties"]
    assert set(properties) == {"resource"}


def test_authorization_interrupt_pauses_then_resumes() -> None:
    stub = StubExchangeClient()
    stub.granted = False
    agent = build_agent(stub, authorization_url="https://consent.example/authorize")
    config = {"configurable": {"thread_id": "auth-interrupt"}}

    result = agent.invoke(
        PROMPT, config, context=KeycardIdentity(subject_token="caller-token")
    )
    interrupts = result.get("__interrupt__", [])
    assert len(interrupts) == 1
    payload = interrupts[0].value
    assert payload["type"] == "authorization_required"
    assert payload["authorization_url"] == "https://consent.example/authorize"
    assert payload["resources"] == [RESOURCE]

    stub.granted = True  # the user consented out of band
    # Runtime context is not checkpointed, so a resume re-supplies identity,
    # exactly as a server does on every run.
    result = agent.invoke(
        Command(resume="authorized"),
        config,
        context=KeycardIdentity(subject_token="caller-token"),
    )
    assert f"TOKEN: obo-token-for-{RESOURCE}" in last_tool_message(result).content


def test_sign_in_interrupt_picks_up_identity_without_a_restart() -> None:
    stub = StubExchangeClient()
    signed_in: dict[str, KeycardIdentity | None] = {"identity": None}
    agent = build_agent(
        stub,
        sign_in_url="https://consent.example/",
        authorization_url="https://consent.example/authorize",
        fallback_identity=lambda: signed_in["identity"],
    )
    config = {"configurable": {"thread_id": "sign-in-interrupt"}}

    result = agent.invoke(PROMPT, config)
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "sign_in_required"
    assert payload["sign_in_url"] == "https://consent.example/"
    assert not stub.exchange_calls

    signed_in["identity"] = KeycardIdentity(subject_token="caller-token")
    result = agent.invoke(Command(resume="signed in"), config)
    assert f"TOKEN: obo-token-for-{RESOURCE}" in last_tool_message(result).content


def test_request_scopes_reach_the_exchange() -> None:
    stub = StubExchangeClient()
    agent = build_agent(stub, request_scopes={RESOURCE: ["read", "write"]})
    agent.invoke(PROMPT, context=KeycardIdentity(subject_token="caller-token"))
    assert stub.exchange_calls[0].scope == "read write"


def test_as_self_uses_client_credentials_not_exchange() -> None:
    stub = StubExchangeClient()
    result = build_agent(stub).invoke(PROMPT, context=KeycardIdentity(as_self=True))
    assert f"TOKEN: self-token-for-{RESOURCE}" in last_tool_message(result).content
    assert not stub.exchange_calls
    assert not stub.impersonate_calls
    assert stub.self_calls == [{"resource": RESOURCE}]


def test_as_self_request_scopes_reach_the_grant() -> None:
    stub = StubExchangeClient()
    agent = build_agent(stub, request_scopes={RESOURCE: ["repo:read"]})
    agent.invoke(PROMPT, context=KeycardIdentity(as_self=True))
    assert stub.self_calls == [{"resource": RESOURCE, "scope": "repo:read"}]


def test_as_self_denial_is_an_error_never_an_interrupt() -> None:
    """No user exists to send to a consent page, so as-itself must not pause."""
    stub = StubExchangeClient()
    stub.self_granted = False
    agent = build_agent(
        stub,
        authorization_url="https://consent.example/authorize",
        sign_in_url="https://consent.example/",
    )
    config = {"configurable": {"thread_id": "as-self-denied"}}

    result = agent.invoke(PROMPT, config, context=KeycardIdentity(as_self=True))
    assert not result.get("__interrupt__")
    content = last_tool_message(result).content
    assert "RESOURCE_ERROR" in content
    assert "Client credentials grant failed" in content


def test_zone_url_is_required_without_an_injected_client() -> None:
    with pytest.raises(ValueError, match="zone_url"):
        KeycardGrantMiddleware(resources=[RESOURCE])


def test_expired_subject_token_pauses_for_sign_in_not_consent() -> None:
    """Consent cannot fix an expired token, so it must not route to consent."""
    stub = StubExchangeClient()
    agent = build_agent(
        stub,
        sign_in_url="https://consent.example/",
        authorization_url="https://consent.example/authorize",
    )
    config = {"configurable": {"thread_id": "expired-token"}}

    result = agent.invoke(
        PROMPT,
        config,
        context=KeycardIdentity(subject_token=jwt_with_exp(time.time() - 60)),
    )
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "sign_in_required"
    assert payload["reason"] == "subject_token_expired"
    assert not stub.exchange_calls, "an expired token must not be sent for exchange"


def test_expired_subject_token_without_sign_in_url_is_an_error() -> None:
    stub = StubExchangeClient()
    result = build_agent(stub).invoke(
        PROMPT, context=KeycardIdentity(subject_token=jwt_with_exp(time.time() - 60))
    )
    content = last_tool_message(result).content
    assert "GLOBAL_ERROR" in content
    assert "subject_token_expired" in content
    assert not stub.exchange_calls


def test_unexpired_jwt_subject_token_exchanges_normally() -> None:
    stub = StubExchangeClient()
    token = jwt_with_exp(time.time() + 3600)
    result = build_agent(stub).invoke(
        PROMPT, context=KeycardIdentity(subject_token=token)
    )
    assert f"TOKEN: obo-token-for-{RESOURCE}" in last_tool_message(result).content
    assert stub.exchange_calls[0].subject_token == token


def test_sync_path_runs_on_one_persistent_loop() -> None:
    """A fresh loop per sync call would defeat the per-loop client cache."""
    middleware = KeycardGrantMiddleware(
        resources=[RESOURCE], client=StubExchangeClient()
    )

    async def running_loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    assert middleware._run_sync(running_loop()) is middleware._run_sync(running_loop())


def test_grant_serves_tools_outside_the_agent() -> None:
    stub = StubExchangeClient()
    middleware = KeycardGrantMiddleware(resources=[RESOURCE], client=stub)

    with middleware.grant(KeycardIdentity(subject_token="caller-token")) as access:
        assert not access.has_errors()
        result = read_delegated_token.invoke({"resource": RESOURCE})
    assert f"TOKEN: obo-token-for-{RESOURCE}" in result

    with pytest.raises(RuntimeError, match="KeycardGrantMiddleware"):
        read_delegated_token.invoke({"resource": RESOURCE})


async def test_agrant_serves_tools_on_the_async_path() -> None:
    stub = StubExchangeClient()
    middleware = KeycardGrantMiddleware(resources=[RESOURCE], client=stub)

    async with middleware.agrant(KeycardIdentity(as_self=True)) as access:
        assert access.access(RESOURCE).access_token == f"self-token-for-{RESOURCE}"
        result = await read_delegated_token.ainvoke({"resource": RESOURCE})
    assert f"TOKEN: self-token-for-{RESOURCE}" in result


def test_grant_uses_the_fallback_identity_when_omitted() -> None:
    stub = StubExchangeClient()
    middleware = KeycardGrantMiddleware(
        resources=[RESOURCE],
        client=stub,
        fallback_identity=KeycardIdentity(as_self=True),
    )
    with middleware.grant() as access:
        assert access.access(RESOURCE).access_token == f"self-token-for-{RESOURCE}"


def test_grant_applies_the_tool_resources_override() -> None:
    stub = StubExchangeClient()
    middleware = KeycardGrantMiddleware(
        resources=["https://other.example.test"],
        tool_resources={"read_delegated_token": [RESOURCE]},
        client=stub,
    )
    with middleware.grant(
        KeycardIdentity(subject_token="caller-token"), tool_name="read_delegated_token"
    ) as access:
        assert access.access(RESOURCE).access_token == f"obo-token-for-{RESOURCE}"
    assert [c.resource for c in stub.exchange_calls] == [RESOURCE]


def test_grant_records_missing_identity_instead_of_raising() -> None:
    middleware = KeycardGrantMiddleware(
        resources=[RESOURCE], client=StubExchangeClient()
    )
    with middleware.grant() as access:
        assert access.has_error()
        assert access.get_error()["code"] == "missing_identity"


class StubAssertionCredential:
    """ApplicationCredential whose proof rides in the request body,
    the shape WorkloadIdentity and WebIdentity use."""

    def get_http_client_auth(self):  # noqa: ANN201
        from keycardai.oauth import NoneAuth

        return NoneAuth()

    def set_client_config(self, config, auth_info):  # noqa: ANN001, ANN201
        return config

    async def prepare_token_exchange_request(
        self, client, subject_token: str, resource: str, auth_info=None
    ):  # noqa: ANN001, ANN201
        return TokenExchangeRequest(
            subject_token=subject_token,
            resource=resource,
            subject_token_type="urn:ietf:params:oauth:token-type:access_token",
            client_assertion="stub-assertion",
            client_assertion_type="urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        )


def test_client_id_and_secret_are_client_secret_shorthand() -> None:
    """The two spellings must be one object: the params build the same
    ClientSecret a caller would pass as application_credential."""
    from keycardai.oauth.server.credentials import ClientSecret

    stub = StubExchangeClient()
    middleware = KeycardGrantMiddleware(
        resources=[RESOURCE],
        client=stub,
        client_id="agent",
        client_secret="s3cret",
    )
    assert isinstance(middleware._credential, ClientSecret)

    with middleware.grant(KeycardIdentity(subject_token="caller-token")) as access:
        assert access.access(RESOURCE).access_token == f"obo-token-for-{RESOURCE}"
    request = stub.exchange_calls[0]
    assert request.subject_token == "caller-token"
    assert request.client_assertion is None


def test_credential_and_client_id_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="not both"):
        KeycardGrantMiddleware(
            zone_url="https://zone.example",
            resources=[RESOURCE],
            application_credential=StubAssertionCredential(),
            client_id="agent",
            client_secret="secret",
        )


def test_credential_prepares_the_exchange_request() -> None:
    stub = StubExchangeClient()
    middleware = KeycardGrantMiddleware(
        resources=[RESOURCE],
        client=stub,
        application_credential=StubAssertionCredential(),
    )
    with middleware.grant(KeycardIdentity(subject_token="caller-token")) as access:
        assert access.access(RESOURCE).access_token == f"obo-token-for-{RESOURCE}"
    request = stub.exchange_calls[0]
    assert request.subject_token == "caller-token"
    assert request.client_assertion == "stub-assertion"


def test_credential_assertion_reaches_the_as_self_grant() -> None:
    stub = StubExchangeClient()
    middleware = KeycardGrantMiddleware(
        resources=[RESOURCE],
        client=stub,
        application_credential=StubAssertionCredential(),
    )
    with middleware.grant(KeycardIdentity(as_self=True)) as access:
        assert access.access(RESOURCE).access_token == f"self-token-for-{RESOURCE}"
    call = stub.self_calls[0]
    assert call["resource"] == RESOURCE
    assert call["client_assertion"] == "stub-assertion"
    assert call["client_assertion_type"].endswith("jwt-bearer")


def test_partial_grant_yields_token_and_resource_error_side_by_side() -> None:
    """Partial success is the contract: one denied resource must not poison
    the granted one, and the failure stays per-resource, not global."""
    stub = StubExchangeClient()
    denied = "https://denied.example.test"
    stub.denied_resources.add(denied)
    middleware = KeycardGrantMiddleware(resources=[RESOURCE, denied], client=stub)

    with middleware.grant(KeycardIdentity(subject_token="caller-token")) as access:
        assert access.access(RESOURCE).access_token == f"obo-token-for-{RESOURCE}"
        assert access.has_resource_error(denied)
        assert not access.has_error()


def test_no_tool_executes_before_an_interrupt_resolves() -> None:
    """The pause happens before the handler: an interrupted run must contain
    no ToolMessage, so nothing side-effectful ran pre-consent."""
    stub = StubExchangeClient()
    stub.granted = False
    agent = build_agent(stub, authorization_url="https://consent.example/authorize")
    config = {"configurable": {"thread_id": "no-tool-before-interrupt"}}

    result = agent.invoke(
        PROMPT, config, context=KeycardIdentity(subject_token="caller-token")
    )
    assert result.get("__interrupt__")
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert not tool_messages, "tool ran before authorization resolved"


def test_grant_accepts_explicit_resources_without_a_tool() -> None:
    """A resource with no tool attached, e.g. a vaulted LLM key."""
    stub = StubExchangeClient()
    middleware = KeycardGrantMiddleware(resources=[RESOURCE], client=stub)
    key_resource = "https://llm-key.example.test"

    with middleware.grant(
        KeycardIdentity(as_self=True), resources=[key_resource]
    ) as access:
        assert access.access(key_resource).access_token == (
            f"self-token-for-{key_resource}"
        )
    assert stub.self_calls == [{"resource": key_resource}]


def test_grant_rejects_tool_name_and_resources_together() -> None:
    middleware = KeycardGrantMiddleware(
        resources=[RESOURCE], client=StubExchangeClient()
    )
    with pytest.raises(ValueError, match="not both"):
        with middleware.grant(
            KeycardIdentity(as_self=True),
            tool_name="read_delegated_token",
            resources=[RESOURCE],
        ):
            pass
