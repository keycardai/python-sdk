"""mcp_auth: per-run bearer auth for MCP toolsets, against an in-process
FastMCP server served over HTTP (the framework applies `auth` only to URL
clients). The zone is a stub; the agent loop, toolset and session are real."""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from keycardai.pydantic_ai import Access, KeycardGrant, MCPAuthError, get_access_context

from .test_grant import RESOURCE, StubExchangeClient, jwt_with_exp

MCP_RESOURCE = "https://mcp.example.test"
SEEN_BEARERS: list[str] = []


def build_server() -> FastMCP:
    server = FastMCP("crm")

    @server.tool
    def whoami() -> str:
        """Report the bearer this request carried."""
        auth = get_http_headers(include_all=True).get("authorization", "")
        SEEN_BEARERS.append(auth)
        return auth.removeprefix("Bearer ")

    return server


@pytest.fixture
async def mcp_url() -> AsyncIterator[str]:
    SEEN_BEARERS.clear()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = build_server().http_app(path="/mcp", stateless_http=True)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", lifespan="on"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await task


def call_mcp_tool(times: int = 1):
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        step = sum(1 for m in messages if m.kind == "response")
        if step < times:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="crm_whoami", args={}, tool_call_id=f"call-{step}"
                    )
                ]
            )
        returns = [
            str(part.content)
            for m in messages
            if m.kind == "request"
            for part in m.parts
            if part.part_kind == "tool-return"
        ]
        return ModelResponse(parts=[TextPart(content=" | ".join(returns))])

    return FunctionModel(model)


def make_agent(
    grant: KeycardGrant, url: str, *, times: int = 1, **toolset_kwargs: Any
) -> Agent:
    toolset = MCPToolset(
        url, auth=grant.mcp_auth(MCP_RESOURCE, **toolset_kwargs)
    ).prefixed("crm")
    return Agent(call_mcp_tool(times), toolsets=[toolset], capabilities=[grant])


def grant_with(stub: StubExchangeClient, **kwargs: Any) -> KeycardGrant:
    kwargs.setdefault("resources", [RESOURCE])
    kwargs.setdefault(
        "tool_resources", lambda name: [] if name.startswith("crm_") else None
    )
    return KeycardGrant(client=stub, **kwargs)


def unwrap(exc: BaseException) -> BaseException:
    """The MCPAuthError under FastMCP's "Client failed to connect" wrapper and any groups."""
    seen: list[BaseException] = [exc]
    while seen:
        current = seen.pop()
        if isinstance(current, MCPAuthError):
            return current
        if isinstance(current, BaseExceptionGroup):
            seen.extend(current.exceptions)
        for linked in (current.__cause__, current.__context__):
            if linked is not None and linked is not current:
                seen.append(linked)
    return exc


# Row 47
async def test_two_tool_calls_in_one_run_acquire_once(mcp_url: str) -> None:
    stub = StubExchangeClient()
    agent = make_agent(grant_with(stub), mcp_url, times=2)
    result = await agent.run("go", deps=Access.on_behalf_of("tok"))
    assert result.output == f"obo-tok-for-{MCP_RESOURCE} | obo-tok-for-{MCP_RESOURCE}"
    assert [c.resource for c in stub.exchange_calls] == [MCP_RESOURCE]
    assert all(b == f"Bearer obo-tok-for-{MCP_RESOURCE}" for b in SEEN_BEARERS)


# Row 49: the capability itself acquires nothing for the MCP tools.
async def test_tool_resources_callable_keeps_the_capability_from_acquiring_for_mcp_tools(
    mcp_url: str,
) -> None:
    stub = StubExchangeClient()
    agent = make_agent(
        grant_with(stub, authorization_url="https://zone/consent"), mcp_url
    )
    await agent.run("go", deps=Access.on_behalf_of("tok"))
    assert [c.resource for c in stub.exchange_calls] == [MCP_RESOURCE]


async def test_without_tool_resources_the_capability_also_acquires_default_resources(
    mcp_url: str,
) -> None:
    stub = StubExchangeClient()
    agent = make_agent(KeycardGrant(client=stub, resources=[RESOURCE]), mcp_url)
    await agent.run("go", deps=Access.on_behalf_of("tok"))
    assert sorted(c.resource for c in stub.exchange_calls) == sorted(
        [MCP_RESOURCE, RESOURCE]
    )


# Row 48
async def test_reacquires_within_the_skew_and_never_sends_the_expired_token(
    mcp_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = StubExchangeClient()
    issued: list[str] = []

    async def exchange_token(request):  # noqa: ANN001, ANN202
        stub.exchange_calls.append(request)
        issued.append(f"tok-{len(issued)}")
        from keycardai.oauth import TokenResponse

        return TokenResponse(
            access_token=issued[-1], token_type="Bearer", expires_in=100
        )

    stub.exchange_token = exchange_token  # type: ignore[method-assign]
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "keycardai.pydantic_ai.grant.time.monotonic", lambda: clock["now"]
    )
    agent = make_agent(grant_with(stub), mcp_url, times=3, expiry_skew_seconds=60)

    original = agent.model.function  # type: ignore[attr-defined]

    def ticking(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # The session's first request acquired tok-0 at t=0 (100s token, fresh
        # until t=40 with a 60s skew); the second tool call lands at t=50.
        clock["now"] += 25
        return original(messages, info)

    agent.model.function = ticking  # type: ignore[attr-defined]
    result = await agent.run("go", deps=Access.on_behalf_of("tok"))
    assert issued == ["tok-0", "tok-1"]
    assert result.output == "tok-0 | tok-1 | tok-1"
    assert "Bearer tok-0" not in SEEN_BEARERS[1:]


# No identity and expired subject token fail the connection, never pause.
@pytest.mark.parametrize(
    ("deps", "code"),
    [(None, "missing_identity"), ("expired", "subject_token_expired")],
)
async def test_missing_or_expired_identity_fails_the_connection(
    mcp_url: str, deps: Any, code: str
) -> None:
    stub = StubExchangeClient()
    agent = make_agent(grant_with(stub, sign_in_url="https://zone/sign-in"), mcp_url)
    identity = (
        Access.on_behalf_of(jwt_with_exp(time.time() - 5))
        if deps == "expired"
        else None
    )
    with pytest.raises(BaseException) as info:
        await agent.run("go", deps=identity)
    error = unwrap(info.value)
    assert isinstance(error, MCPAuthError)
    assert error.code == code and error.resource == MCP_RESOURCE
    assert stub.exchange_calls == [] and SEEN_BEARERS == []


async def test_missing_grant_fails_the_connection_with_the_resource_and_code(
    mcp_url: str,
) -> None:
    stub = StubExchangeClient()
    stub.denied_resources.add(MCP_RESOURCE)
    agent = make_agent(grant_with(stub), mcp_url)
    with pytest.raises(BaseException) as info:
        await agent.run("go", deps=Access.on_behalf_of("tok"))
    error = unwrap(info.value)
    assert isinstance(error, MCPAuthError) and error.resource == MCP_RESOURCE
    assert "tok" not in str(error) and SEEN_BEARERS == []


# Two concurrent runs on one toolset each send their own user's token.
async def test_concurrent_runs_send_their_own_tokens(mcp_url: str) -> None:
    stub = StubExchangeClient()
    agent = make_agent(grant_with(stub), mcp_url)
    a, b = await asyncio.gather(
        agent.run("go", deps=Access.on_behalf_of("a")),
        agent.run("go", deps=Access.on_behalf_of("b")),
    )
    assert (
        a.output == f"obo-a-for-{MCP_RESOURCE}"
        and b.output == f"obo-b-for-{MCP_RESOURCE}"
    )
    assert sorted(SEEN_BEARERS) == sorted(
        [f"Bearer obo-a-for-{MCP_RESOURCE}", f"Bearer obo-b-for-{MCP_RESOURCE}"]
    )


# Scopes default to the capability's entry for the resource; per-call override wins.
async def test_request_scopes_default_and_override() -> None:
    stub = StubExchangeClient()
    grant = grant_with(stub, request_scopes={MCP_RESOURCE: ["crm.read"]})
    ctx = SimpleNamespace(deps=Access.on_behalf_of("tok"), agent=None)
    auth = await grant.mcp_auth(MCP_RESOURCE)(ctx)  # type: ignore[arg-type]
    await auth.token()
    assert stub.exchange_calls[-1].scope == "crm.read"
    auth = await grant.mcp_auth(MCP_RESOURCE, request_scopes=["crm.write"])(ctx)  # type: ignore[arg-type]
    await auth.token()
    assert stub.exchange_calls[-1].scope == "crm.write"


# Concurrent first requests in one session share one acquisition.
async def test_concurrent_first_requests_share_one_acquisition() -> None:
    stub = StubExchangeClient()
    grant = grant_with(stub)
    ctx = SimpleNamespace(deps=Access.on_behalf_of("tok"), agent=None)
    auth = await grant.mcp_auth(MCP_RESOURCE)(ctx)  # type: ignore[arg-type]
    tokens = await asyncio.gather(*(auth.token() for _ in range(5)))
    assert (
        set(tokens) == {f"obo-tok-for-{MCP_RESOURCE}"} and len(stub.exchange_calls) == 1
    )


# Row 46, MCP half: a durability capability raises even where in_durable_context is false.
async def test_durability_capability_fails_mcp_auth_before_acquiring() -> None:
    from pydantic_ai.durable_exec import BaseDurabilityCapability
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
    durable_agent = SimpleNamespace(root_capability=FakeDurability())
    ctx = RunContext(deps=Access.as_self(), model=None, usage=None, agent=durable_agent)  # type: ignore[arg-type]
    assert ctx.in_durable_context is False
    with pytest.raises(RuntimeError, match="durable execution"):
        await grant.mcp_auth(MCP_RESOURCE)(ctx)
    assert stub.self_calls == []


def test_mcp_auth_requires_a_resource() -> None:
    with pytest.raises(ValueError, match="resource"):
        grant_with(StubExchangeClient()).mcp_auth(" ")


def test_get_access_context_is_not_what_mcp_tools_use() -> None:
    with pytest.raises(RuntimeError):
        get_access_context()
