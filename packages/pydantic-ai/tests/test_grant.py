"""KeycardGrant behavior, exercised through a real Agent loop.

The exchange client is a stub, so no zone or network is involved; everything
else (the agent, FunctionModel, the capability hooks, the tool call) is real.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import (
    Agent,
    ApprovalRequired,
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
    ToolDenied,
)
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from keycardai.oauth import TokenResponse
from keycardai.oauth.server import AccessContext
from keycardai.oauth.types.models import TokenExchangeRequest
from keycardai.pydantic_ai import (
    Access,
    KeycardGrant,
    KeycardIdentity,
    get_access_context,
    resume_results,
)
from keycardai.pydantic_ai.grant import PAUSE_MARKER, RESUME_MARKER

RESOURCE = "https://api.example.test"
OTHER = "https://other.example.test"
TOOL_BODY_RUNS: list[str] = []


def jwt_with_exp(exp: float) -> str:
    def b64(part: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(part).encode())
        return raw.rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64({'exp': exp})}.sig"


class StubExchangeClient:
    """Stands in for keycardai.oauth.AsyncClient on the exchange paths."""

    def __init__(self) -> None:
        self.exchange_calls: list[TokenExchangeRequest] = []
        self.impersonate_calls: list[dict[str, str]] = []
        self.self_calls: list[dict[str, Any]] = []
        self.granted = True
        self.self_granted = True
        self.denied_resources: set[str] = set()

    async def exchange_token(self, request: TokenExchangeRequest) -> TokenResponse:
        self.exchange_calls.append(request)
        if not self.granted or request.resource in self.denied_resources:
            raise RuntimeError("no grant for this resource yet")
        return TokenResponse(
            access_token=f"obo-{request.subject_token}-for-{request.resource}",
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


def describe(access: AccessContext, resource: str) -> str:
    if access.has_error():
        return f"GLOBAL_ERROR: {access.get_error()}"
    if access.has_resource_error(resource):
        return f"RESOURCE_ERROR: {access.get_resource_error(resource)}"
    if resource not in access.get_successful_resources():
        return "NO_TOKEN"
    return access.access(resource).access_token


def call_tools_once(*calls: tuple[str, dict[str, Any]]):
    """A FunctionModel that issues the given tool calls on the first step and
    echoes the tool results as text on the second."""

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(tool_name=name, args=args, tool_call_id=f"call-{i}")
                    for i, (name, args) in enumerate(calls)
                ]
            )
        returns = [
            str(part.content)
            for part in messages[-1].parts
            if getattr(part, "part_kind", "") in ("tool-return", "retry-prompt")
        ]
        return ModelResponse(parts=[TextPart(content=" | ".join(returns))])

    return FunctionModel(model)


def make_agent(
    grant: KeycardGrant, *calls, output_type: Any = str, deps_type: Any = Any
):
    calls = calls or (("read_token", {"resource": RESOURCE}),)
    agent = Agent(
        call_tools_once(*calls),
        deps_type=deps_type,
        output_type=output_type,
        capabilities=[grant],
    )

    @agent.tool_plain
    async def read_token(resource: str) -> str:
        """Read the delegated token for a resource."""
        TOOL_BODY_RUNS.append(resource)
        return describe(get_access_context(), resource)

    @agent.tool_plain
    def read_token_sync(resource: str) -> str:
        """Read the delegated token for a resource from a worker thread."""
        TOOL_BODY_RUNS.append(resource)
        return f"{threading.current_thread().name}:{describe(get_access_context(), resource)}"

    @agent.tool
    async def read_token_ctx(ctx: RunContext[Any], resource: str) -> str:
        """Read the delegated token, declaring RunContext first."""
        return describe(get_access_context(), resource)

    return agent


@pytest.fixture(autouse=True)
def _clear_runs():
    TOOL_BODY_RUNS.clear()


def grant_with(stub: StubExchangeClient, **kwargs) -> KeycardGrant:
    kwargs.setdefault("resources", [RESOURCE])
    return KeycardGrant(client=stub, **kwargs)


# Rows 1, 11, 35: on-behalf-of through sync and async tools.
async def test_on_behalf_of_exchanges_the_callers_token_for_sync_and_async_tools():
    stub = StubExchangeClient()
    agent = make_agent(
        grant_with(stub),
        ("read_token", {"resource": RESOURCE}),
        ("read_token_sync", {"resource": RESOURCE}),
    )
    result = await agent.run(
        "go", deps=Access.on_behalf_of(jwt_with_exp(time.time() + 600))
    )
    assert result.output.count(f"-for-{RESOURCE}") == 2
    assert "MainThread" not in result.output
    assert len(stub.exchange_calls) == 2
    assert all(c.resource == RESOURCE for c in stub.exchange_calls)


# Row 2
async def test_impersonation_uses_the_substitute_user_path():
    stub = StubExchangeClient()
    result = await make_agent(grant_with(stub)).run(
        "go", deps=Access.impersonate("alice")
    )
    assert result.output == f"impersonated-alice-for-{RESOURCE}"
    assert stub.exchange_calls == []


# Rows 3, 4
async def test_as_self_uses_client_credentials_with_request_scopes():
    stub = StubExchangeClient()
    grant = grant_with(stub, request_scopes={RESOURCE: ["read", "write"]})
    result = await make_agent(grant).run("go", deps=Access.as_self())
    assert result.output == f"self-token-for-{RESOURCE}"
    assert stub.exchange_calls == [] and stub.impersonate_calls == []
    assert stub.self_calls[0]["scope"] == "read write"


# Row 5
async def test_on_behalf_of_request_scopes_reach_the_exchange():
    stub = StubExchangeClient()
    grant = grant_with(stub, request_scopes={RESOURCE: ["read", "write"]})
    await make_agent(grant).run("go", deps=Access.on_behalf_of("tok"))
    assert stub.exchange_calls[0].scope == "read write"


# Rows 6, 32 (third case), 34
async def test_missing_identity_is_recorded_not_raised():
    stub = StubExchangeClient()
    result = await make_agent(grant_with(stub)).run("go", deps=object())
    assert "GLOBAL_ERROR" in result.output and "missing_identity" in result.output
    assert "deps=" in result.output
    assert stub.exchange_calls == []


async def test_identity_on_run_metadata_only_is_not_read():
    stub = StubExchangeClient()
    agent = make_agent(grant_with(stub))
    result = await agent.run(
        "go", deps=None, metadata={"keycard_identity": Access.on_behalf_of("tok")}
    )
    assert "missing_identity" in result.output
    assert stub.exchange_calls == []


# Row 32
@dataclass
class AppDeps:
    user: str
    keycard_identity: KeycardIdentity | None = None


async def test_identity_resolves_from_deps_itself_or_deps_keycard_identity():
    stub = StubExchangeClient()
    agent = make_agent(grant_with(stub), deps_type=Any)
    assert "obo-a-for" in (await agent.run("go", deps=Access.on_behalf_of("a"))).output
    assert (
        "obo-b-for"
        in (await agent.run("go", deps=AppDeps("u", Access.on_behalf_of("b")))).output
    )


# Row 33
async def test_identity_from_callable_runs_per_tool_call_and_fallback_fills_none():
    stub = StubExchangeClient()
    seen: list[str] = []

    def identity_from(ctx: RunContext[Any]) -> KeycardIdentity | None:
        seen.append(ctx.tool_name or "")
        return ctx.deps.get("identity")

    grant = grant_with(
        stub, identity_from=identity_from, fallback_identity=Access.as_self()
    )
    agent = make_agent(grant)
    assert (
        "obo-x-for"
        in (await agent.run("go", deps={"identity": Access.on_behalf_of("x")})).output
    )
    assert "self-token" in (await agent.run("go", deps={})).output
    assert seen == ["read_token", "read_token"]


# Row 7
def test_tool_schema_carries_no_keycard_fields():
    agent = make_agent(grant_with(StubExchangeClient()))
    for name in ("read_token", "read_token_ctx"):
        schema = agent._function_toolset.tools[name].function_schema.json_schema
        assert set(schema["properties"]) == {"resource"}


# Row 8
async def test_partial_grant_yields_token_and_resource_error_side_by_side():
    stub = StubExchangeClient()
    stub.denied_resources.add(OTHER)
    agent = make_agent(
        grant_with(stub, resources=[RESOURCE, OTHER]),
        ("read_token", {"resource": RESOURCE}),
        ("read_token", {"resource": OTHER}),
    )
    out = (await agent.run("go", deps=Access.on_behalf_of("tok"))).output
    assert f"obo-tok-for-{RESOURCE}" in out and "RESOURCE_ERROR" in out
    assert "GLOBAL_ERROR" not in out


# Rows 9, 38: sign-in pauses, tool body never runs.
async def test_expired_subject_token_pauses_for_sign_in():
    stub = StubExchangeClient()
    agent = make_agent(
        grant_with(stub, sign_in_url="https://zone/sign-in"),
        output_type=[str, DeferredToolRequests],
    )
    result = await agent.run(
        "go", deps=Access.on_behalf_of(jwt_with_exp(time.time() - 5))
    )
    assert isinstance(result.output, DeferredToolRequests)
    payload = result.output.metadata["call-0"]
    assert payload["type"] == "sign_in_required"
    assert payload["reason"] == "subject_token_expired"
    assert payload["sign_in_url"] == "https://zone/sign-in"
    assert stub.exchange_calls == [] and TOOL_BODY_RUNS == []


async def test_missing_identity_with_sign_in_url_pauses_with_reason_missing_identity():
    agent = make_agent(
        grant_with(StubExchangeClient(), sign_in_url="https://zone/sign-in"),
        output_type=[str, DeferredToolRequests],
    )
    result = await agent.run("go", deps=None)
    assert isinstance(result.output, DeferredToolRequests)
    assert result.output.metadata["call-0"]["reason"] == "missing_identity"
    assert [c.tool_call_id for c in result.output.approvals] == ["call-0"]
    assert TOOL_BODY_RUNS == []


# Row 10
async def test_expired_subject_token_without_sign_in_url_records_an_error():
    stub = StubExchangeClient()
    out = (
        await make_agent(grant_with(stub)).run(
            "go", deps=Access.on_behalf_of(jwt_with_exp(time.time() - 5))
        )
    ).output
    assert "subject_token_expired" in out and stub.exchange_calls == []


# Rows 39, 40, 41, 42: consent pause and the resume paths.
def consent_agent(stub: StubExchangeClient, **kwargs):
    grant = grant_with(stub, authorization_url="https://zone/consent", **kwargs)
    return make_agent(grant, output_type=[str, DeferredToolRequests])


async def test_ungranted_resource_pauses_with_authorization_payload():
    stub = StubExchangeClient()
    stub.granted = False
    result = await consent_agent(stub).run("go", deps=Access.on_behalf_of("tok"))
    payload = result.output.metadata["call-0"]
    assert payload["type"] == "authorization_required"
    assert payload["authorization_url"] == "https://zone/consent"
    assert payload["resources"] == [RESOURCE] and RESOURCE in payload["errors"]
    assert TOOL_BODY_RUNS == []


async def test_approved_resume_reacquires_and_runs_the_tool():
    stub = StubExchangeClient()
    stub.granted = False
    agent = consent_agent(stub)
    paused = await agent.run("go", deps=Access.on_behalf_of("tok"))
    stub.granted = True
    result = await agent.run(
        message_history=paused.all_messages(),
        deps=Access.on_behalf_of("tok"),
        deferred_tool_results=DeferredToolResults(approvals={"call-0": True}),
    )
    assert result.output == f"obo-tok-for-{RESOURCE}"
    assert len(stub.exchange_calls) == 2 and TOOL_BODY_RUNS == [RESOURCE]


async def test_approved_resume_that_still_fails_ends_with_tool_failed_not_a_second_pause():
    stub = StubExchangeClient()
    stub.granted = False
    agent = consent_agent(stub)
    paused = await agent.run("go", deps=Access.on_behalf_of("tok"))
    result = await agent.run(
        message_history=paused.all_messages(),
        deps=Access.on_behalf_of("tok"),
        deferred_tool_results=DeferredToolResults(approvals={"call-0": True}),
    )
    assert isinstance(result.output, str)
    assert "https://zone/consent" in result.output
    assert TOOL_BODY_RUNS == []


async def test_denied_resume_never_runs_the_tool_or_acquires():
    stub = StubExchangeClient()
    stub.granted = False
    agent = consent_agent(stub)
    paused = await agent.run("go", deps=Access.on_behalf_of("tok"))
    calls_before = len(stub.exchange_calls)
    result = await agent.run(
        message_history=paused.all_messages(),
        deps=Access.on_behalf_of("tok"),
        deferred_tool_results=DeferredToolResults(
            approvals={"call-0": ToolDenied("no")}
        ),
    )
    assert result.output == "no"
    assert len(stub.exchange_calls) == calls_before and TOOL_BODY_RUNS == []


# Row 43
async def test_defer_on_auth_false_fails_the_call_with_the_url_instead_of_pausing():
    stub = StubExchangeClient()
    agent = make_agent(
        grant_with(stub, sign_in_url="https://zone/sign-in", defer_on_auth=False),
        output_type=[str, DeferredToolRequests],
    )
    result = await agent.run("go", deps=None)
    assert isinstance(result.output, str) and "https://zone/sign-in" in result.output
    assert TOOL_BODY_RUNS == []


# Approval-gated tools (SDK-85): the framework asks for its approval before
# KeycardGrant runs, so the re-run arrives already approved.
def gated_agent(stub: StubExchangeClient):
    grant = grant_with(stub, authorization_url="https://zone/consent")
    agent = Agent(
        call_tools_once(("send", {})),
        output_type=[str, DeferredToolRequests],
        capabilities=[grant],
    )

    @agent.tool_plain(requires_approval=True)
    async def send() -> str:
        """Send, once the user approves."""
        TOOL_BODY_RUNS.append("send")
        return describe(get_access_context(), RESOURCE)

    return agent


async def test_approval_gated_tool_gets_one_consent_pause_through_resume_results():
    stub = StubExchangeClient()
    stub.granted = False
    agent = gated_agent(stub)
    deps = Access.on_behalf_of("tok")

    approval = await agent.run("go", deps=deps)
    assert isinstance(approval.output, DeferredToolRequests)
    assert "type" not in approval.output.metadata.get("call-0", {})
    assert stub.exchange_calls == []

    consent = await agent.run(
        message_history=approval.all_messages(),
        deps=deps,
        deferred_tool_results=resume_results(approval.output, approve_all=True),
    )
    assert isinstance(consent.output, DeferredToolRequests)
    payload = consent.output.metadata["call-0"]
    assert payload["type"] == "authorization_required"
    assert payload[PAUSE_MARKER] is True
    assert TOOL_BODY_RUNS == []

    stub.granted = True
    done = await agent.run(
        message_history=consent.all_messages(),
        deps=deps,
        deferred_tool_results=resume_results(consent.output, approve_all=True),
    )
    assert done.output == f"obo-tok-for-{RESOURCE}"
    assert TOOL_BODY_RUNS == ["send"]


async def test_approval_gated_tool_without_resume_results_fails_instead_of_pausing():
    stub = StubExchangeClient()
    stub.granted = False
    agent = gated_agent(stub)
    deps = Access.on_behalf_of("tok")
    approval = await agent.run("go", deps=deps)
    result = await agent.run(
        message_history=approval.all_messages(),
        deps=deps,
        deferred_tool_results=approval.output.build_results(approve_all=True),
    )
    assert isinstance(result.output, str)
    assert "https://zone/consent" in result.output
    assert TOOL_BODY_RUNS == []


async def test_own_pause_resumed_through_resume_results_never_pauses_twice():
    stub = StubExchangeClient()
    stub.granted = False
    agent = consent_agent(stub)
    deps = Access.on_behalf_of("tok")
    paused = await agent.run("go", deps=deps)
    assert paused.output.metadata["call-0"][PAUSE_MARKER] is True
    result = await agent.run(
        message_history=paused.all_messages(),
        deps=deps,
        deferred_tool_results=resume_results(paused.output, approve_all=True),
    )
    assert isinstance(result.output, str)
    assert "https://zone/consent" in result.output
    assert TOOL_BODY_RUNS == []


def test_resume_results_hands_metadata_back_and_merges_the_hosts():
    from pydantic_ai import ToolApproved

    requests = DeferredToolRequests(
        approvals=[ToolCallPart(tool_name="t", args={}, tool_call_id="a")],
        metadata={"a": {"type": "authorization_required"}},
    )
    results = resume_results(
        requests, approve_all=True, metadata={"a": {"host": 1}, "b": {"other": 2}}
    )
    assert results.metadata["a"] == {
        "type": "authorization_required",
        "host": 1,
        RESUME_MARKER: True,
    }
    assert results.metadata["b"] == {"other": 2}
    assert isinstance(results.approvals["a"], ToolApproved)


# Row 44
async def test_as_self_denial_never_pauses():
    stub = StubExchangeClient()
    stub.self_granted = False
    agent = consent_agent(stub, sign_in_url="https://zone/sign-in")
    result = await agent.run("go", deps=Access.as_self())
    assert isinstance(result.output, str) and "RESOURCE_ERROR" in result.output


# Row 36
async def test_parallel_tool_calls_each_see_their_own_access_context():
    stub = StubExchangeClient()
    agent = make_agent(
        grant_with(
            stub,
            resources=[RESOURCE],
            tool_resources={"read_token_sync": [OTHER]},
        ),
        ("read_token", {"resource": RESOURCE}),
        ("read_token_sync", {"resource": OTHER}),
    )
    out = (await agent.run("go", deps=Access.on_behalf_of("tok"))).output
    first, second = out.split(" | ")
    assert first == f"obo-tok-for-{RESOURCE}"
    assert second.endswith(f"obo-tok-for-{OTHER}")


# Row 37
async def test_concurrent_runs_exchange_their_own_identity():
    stub = StubExchangeClient()
    agent = make_agent(grant_with(stub))
    a, b = await asyncio.gather(
        agent.run("go", deps=Access.on_behalf_of("a")),
        agent.run("go", deps=Access.on_behalf_of("b")),
    )
    assert a.output == f"obo-a-for-{RESOURCE}" and b.output == f"obo-b-for-{RESOURCE}"


# Row 49
async def test_tool_resources_callable_returning_empty_acquires_nothing():
    stub = StubExchangeClient()

    def select(tool_name: str) -> list[str] | None:
        return [] if tool_name.startswith("read_token_sync") else None

    agent = make_agent(
        grant_with(
            stub, tool_resources=select, authorization_url="https://zone/consent"
        ),
        ("read_token_sync", {"resource": RESOURCE}),
        ("read_token", {"resource": RESOURCE}),
        output_type=[str, DeferredToolRequests],
    )
    out = (await agent.run("go", deps=Access.on_behalf_of("tok"))).output
    assert isinstance(out, str)
    assert len(stub.exchange_calls) == 1
    assert "NO_TOKEN" in out and f"obo-tok-for-{RESOURCE}" in out


# Row 46 (capability half): checked on the agent, not on in_durable_context,
# which a durability engine reports false inside its activities.
def test_durability_capability_raises_before_acquiring():
    from pydantic_ai.durable_exec._base import BaseDurabilityCapability
    from pydantic_ai.durable_exec._spec import DurabilityEngineSpec

    class FakeDurability(BaseDurabilityCapability[Any]):
        engine_spec = DurabilityEngineSpec(
            engine_name="FakeEngine",
            durable_unit_noun="step",
            durable_container_noun="flow",
        )

        @property
        def in_durable_context(self) -> bool:
            return False

        def get_durable_operation_backend(self):  # noqa: ANN201
            raise NotImplementedError

    stub = StubExchangeClient()
    grant = grant_with(stub)
    # from_agent walks agent.root_capability; an unbound fake stands in for the
    # worker-registered engine (binding a real one needs a durable backend).
    durable_agent = SimpleNamespace(root_capability=FakeDurability())
    ctx = RunContext(deps=Access.as_self(), model=None, usage=None, agent=durable_agent)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="durable execution"):
        grant._check_durable(ctx)
    assert stub.self_calls == []
    plain = Agent(call_tools_once(), capabilities=[grant])
    grant._check_durable(
        RunContext(deps=None, model=plain.model, usage=None, agent=plain)
    )  # type: ignore[arg-type]


# Rows 12, 14
def test_configuration_errors():
    with pytest.raises(Exception, match="zone_url"):
        KeycardGrant(resources=[RESOURCE], client_id="a", client_secret="b")
    with pytest.raises(ValueError, match="application_credential"):
        KeycardGrant(
            client=StubExchangeClient(),
            resources=[RESOURCE],
            client_id="a",
            client_secret="b",
            application_credential=object(),  # type: ignore[arg-type]
        )


# Row 13
def test_client_id_and_secret_are_client_secret_shorthand():
    from keycardai.oauth.server.credentials import ClientSecret

    stub = StubExchangeClient()
    grant = grant_with(stub, client_id="agent", client_secret="s3cret")
    assert isinstance(grant.engine.credential, ClientSecret)
    with grant.grant(Access.on_behalf_of("caller-token")) as access:
        assert (
            access.access(RESOURCE).access_token == f"obo-caller-token-for-{RESOURCE}"
        )
    assert stub.exchange_calls[0].client_assertion is None


# Rows 18 to 24: grant() / agrant()
def test_grant_serves_tools_outside_the_agent_and_not_after():
    stub = StubExchangeClient()
    grant = grant_with(stub, tool_resources={"other_tool": [OTHER]})
    with grant.grant(Access.on_behalf_of("tok")):
        assert (
            get_access_context().access(RESOURCE).access_token
            == f"obo-tok-for-{RESOURCE}"
        )
    with pytest.raises(RuntimeError, match="KeycardGrant"):
        get_access_context()
    with grant.grant(Access.on_behalf_of("tok"), tool_name="other_tool") as access:
        assert access.access(OTHER).access_token == f"obo-tok-for-{OTHER}"
    with grant.grant(Access.on_behalf_of("tok"), resources=["https://x"]) as access:
        assert access.get_successful_resources() == ["https://x"]
    with pytest.raises(ValueError, match="tool_name"):
        with grant.grant(Access.on_behalf_of("tok"), tool_name="a", resources=["b"]):
            pass


async def test_agrant_as_self_on_the_running_loop():
    stub = StubExchangeClient()
    async with grant_with(stub).agrant(Access.as_self()) as access:
        assert access.access(RESOURCE).access_token == f"self-token-for-{RESOURCE}"
        assert get_access_context() is access


def test_grant_without_identity_uses_fallback_or_records_missing_identity():
    stub = StubExchangeClient()
    with grant_with(stub, fallback_identity=Access.as_self()).grant() as access:
        assert access.access(RESOURCE).access_token.startswith("self-token")
    with grant_with(stub).grant() as access:
        assert access.has_error() and access.get_error()["code"] == "missing_identity"


# Rows 25, 26
def test_access_factories():
    assert Access.as_self().as_self is True
    assert Access.on_behalf_of("t").subject_token == "t"
    assert Access.impersonate("u").user_identifier == "u"
    with pytest.raises(TypeError, match="Access"):
        Access()
    for bad in ("", "   "):
        with pytest.raises(ValueError):
            Access.on_behalf_of(bad)
        with pytest.raises(ValueError):
            Access.impersonate(bad)


# Row 17
async def test_two_runs_on_one_loop_share_one_client(monkeypatch):
    built: list[StubExchangeClient] = []

    def build_client(**kwargs: Any) -> StubExchangeClient:
        built.append(StubExchangeClient())
        return built[-1]

    monkeypatch.setattr("keycardai.oauth.server.agent_grant.AsyncClient", build_client)
    grant = KeycardGrant(
        zone_url="https://zone.example.test",
        resources=[RESOURCE],
        client_id="a",
        client_secret="b",
    )
    agent = make_agent(grant)
    for _ in range(2):
        result = await agent.run("go", deps=Access.on_behalf_of("caller-token"))
        assert result.output == f"obo-caller-token-for-{RESOURCE}"
    assert len(built) == 1
    assert len(built[0].exchange_calls) == 2


# Row 31
def test_get_access_context_outside_any_seam_reports_missing_capability():
    with pytest.raises(RuntimeError, match="KeycardGrant"):
        get_access_context()


def test_capability_is_an_abstract_capability():
    assert isinstance(grant_with(StubExchangeClient()), AbstractCapability)
    assert ApprovalRequired is not None


class StubAssertionCredential:
    """ApplicationCredential whose proof rides in the request body, the shape
    WorkloadIdentity and WebIdentity use."""

    def __init__(self, client_id: str | None = "agent") -> None:
        self.client_id = client_id

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
            client_id=self.client_id,
        )


# Row 15
async def test_assertion_credential_reaches_the_exchange():
    stub = StubExchangeClient()
    grant = grant_with(stub, application_credential=StubAssertionCredential())
    out = (await make_agent(grant).run("go", deps=Access.on_behalf_of("tok"))).output
    assert out == f"obo-tok-for-{RESOURCE}"
    assert stub.exchange_calls[0].client_assertion == "stub-assertion"


# Row 16
async def test_assertion_credential_reaches_the_client_credentials_grant():
    stub = StubExchangeClient()
    grant = grant_with(stub, application_credential=StubAssertionCredential())
    await make_agent(grant).run("go", deps=Access.as_self())
    call = stub.self_calls[0]
    assert call["client_assertion"] == "stub-assertion"
    assert call["client_assertion_type"].endswith("jwt-bearer")


# Row 45, without HandleDeferredToolCalls: the framework's own error, nothing added.
async def test_pause_without_deferred_tool_requests_in_output_type_is_the_frameworks_error():
    from pydantic_ai.exceptions import UserError

    agent = make_agent(
        grant_with(StubExchangeClient(), sign_in_url="https://zone/sign-in")
    )
    with pytest.raises(UserError, match="DeferredToolRequests"):
        await agent.run("go", deps=None)
    assert TOOL_BODY_RUNS == []


# Row 45, with HandleDeferredToolCalls: the pause is resolved inside the run,
# and the approved re-run re-acquires.
async def test_handle_deferred_tool_calls_resolves_the_pause_in_run():
    from pydantic_ai.capabilities import HandleDeferredToolCalls

    stub = StubExchangeClient()
    stub.granted = False
    seen: list[dict[str, Any]] = []

    async def approve(
        ctx: RunContext[Any], requests: DeferredToolRequests
    ) -> DeferredToolResults:
        seen.append(requests.metadata["call-0"])
        stub.granted = True
        return requests.build_results(approve_all=True)

    grant = grant_with(stub, authorization_url="https://zone/consent")
    agent = Agent(
        call_tools_once(("read_token", {"resource": RESOURCE})),
        capabilities=[grant, HandleDeferredToolCalls(handler=approve)],
    )

    @agent.tool_plain
    async def read_token(resource: str) -> str:
        """Read."""
        TOOL_BODY_RUNS.append(resource)
        return describe(get_access_context(), resource)

    result = await agent.run("go", deps=Access.on_behalf_of("tok"))
    assert result.output == f"obo-tok-for-{RESOURCE}"
    assert seen[0]["type"] == "authorization_required"
    assert TOOL_BODY_RUNS == [RESOURCE] and len(stub.exchange_calls) == 2
