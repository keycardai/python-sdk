"""Interrupt mode in the LangChain adapter.

The point of the mode is that an agent combining keycardai-langchain's
KeycardGrantMiddleware (brokered REST tools) with this adapter (MCP tools)
raises one `authorization_required` payload, not two shapes. So these tests
compare against the middleware's own `_interrupt_payload` where it is
installed, and exercise the interrupt through a real LangGraph run rather than
a stubbed `interrupt`.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("langchain")
pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from mcp.types import Tool  # noqa: E402
from typing_extensions import TypedDict  # noqa: E402

from keycardai.mcp.client.integrations.langchain_agents import (  # noqa: E402
    LangChainClient,
    build_authorization_interrupt_payload,
)

AUTH_URL = "https://zone.example/authorize?state=abc"

LIST_ISSUES = Tool(
    name="list_issues",
    description="List issues",
    inputSchema={
        "type": "object",
        "properties": {
            "state": {"type": "string", "description": "Issue state"},
            "team": {"type": "string", "description": "Team"},
        },
    },
)


class FakeSession:
    def __init__(self, requires_user_action: bool):
        self.requires_user_action = requires_user_action


class FakeToolInfo:
    def __init__(self, tool: Tool, server: str):
        self.tool = tool
        self.server = server


class FakeClient:
    """The client surface the adapter uses, with a scriptable auth state."""

    def __init__(self, *, requires_user_action: bool, tools: list[Tool] | None = None):
        self.sessions = {"linear": FakeSession(requires_user_action)}
        self._tools = tools if tools is not None else [LIST_ISSUES]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def connect(self, *args, **kwargs) -> None:
        return None

    async def get_auth_challenges(self, server_name: str | None = None):
        if not self.sessions["linear"].requires_user_action:
            return []
        return [{"server": "linear", "authorization_url": AUTH_URL, "state": "abc"}]

    async def list_tools(self, server_name: str | None = None):
        return [FakeToolInfo(tool, "linear") for tool in self._tools]

    async def call_tool(self, tool_name: str, arguments: dict, server_name=None):
        self.calls.append((tool_name, arguments))
        return "ok"


class RunState(TypedDict):
    result: str


async def run_tool_in_graph(tool, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke a tool inside a checkpointed graph and return the run's output.

    A LangGraph interrupt only exists inside a run, so the tool has to be
    called from a node for the interrupt path to be the real one.
    """

    async def node(state: RunState) -> RunState:
        return {"result": await tool.coroutine(**arguments)}

    graph = StateGraph(RunState)
    graph.add_node("call", node)
    graph.add_edge(START, "call")
    graph.add_edge("call", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    return await compiled.ainvoke({"result": ""}, {"configurable": {"thread_id": "t1"}})


@pytest.fixture
def pending_client() -> FakeClient:
    return FakeClient(requires_user_action=True)


def test_payload_shape_matches_the_middleware(pending_client: FakeClient) -> None:
    """Same keys, same `type`, same message as KeycardGrantMiddleware emits."""
    middleware_module = pytest.importorskip("keycardai.langchain.middleware")

    access = MagicMock()
    access.get_resource_error.return_value = {"code": "access_denied"}
    middleware = middleware_module.KeycardGrantMiddleware(
        zone_url="https://zone.example",
        resources=["https://api.example"],
        authorization_url=AUTH_URL,
    )
    reference = middleware._interrupt_payload(["https://api.example"], access)

    payload = build_authorization_interrupt_payload(
        [{"server": "linear", "authorization_url": AUTH_URL}]
    )

    assert payload.keys() == reference.keys()
    assert payload["type"] == reference["type"] == "authorization_required"
    assert payload["message"] == reference["message"]
    assert payload["authorization_url"] == AUTH_URL
    assert payload["resources"] == ["linear"]
    assert set(payload["errors"]) == {"linear"}


@pytest.mark.asyncio
async def test_interrupt_mode_pauses_the_run(pending_client: FakeClient) -> None:
    client = LangChainClient(pending_client, interrupt_on_auth=True)
    tool = client._convert_mcp_tool_to_langchain(LIST_ISSUES, "linear")

    result = await run_tool_in_graph(tool, {"state": "in progress"})

    (paused,) = result["__interrupt__"]
    assert paused.value == build_authorization_interrupt_payload(
        [{"server": "linear", "authorization_url": AUTH_URL}]
    )
    assert pending_client.calls == [], "tool ran despite the pending challenge"


@pytest.mark.asyncio
async def test_interrupt_mode_calls_through_once_authorized() -> None:
    authorized = FakeClient(requires_user_action=False)
    client = LangChainClient(authorized, interrupt_on_auth=True)
    tool = client._convert_mcp_tool_to_langchain(LIST_ISSUES, "linear")

    result = await run_tool_in_graph(tool, {"state": "in progress"})

    assert result["result"] == "ok"
    assert authorized.calls == [("list_issues", {"state": "in progress"})]


@pytest.mark.asyncio
async def test_default_mode_does_not_interrupt(pending_client: FakeClient) -> None:
    """Off by default: the same pending challenge changes nothing."""
    client = LangChainClient(pending_client)
    tool = client._convert_mcp_tool_to_langchain(LIST_ISSUES, "linear")

    result = await run_tool_in_graph(tool, {"state": "in progress"})

    assert "__interrupt__" not in result
    assert result["result"] == "ok"
    assert pending_client.calls == [("list_issues", {"state": "in progress"})]


@pytest.mark.asyncio
async def test_default_mode_still_offers_the_auth_tool(
    pending_client: FakeClient,
) -> None:
    client = LangChainClient(pending_client)
    async with client:
        tools = await client.get_auth_tools()

    assert [t.name for t in tools] == ["request_authentication"]


@pytest.mark.asyncio
async def test_interrupt_mode_replaces_the_auth_tool(
    pending_client: FakeClient,
) -> None:
    client = LangChainClient(pending_client, interrupt_on_auth=True)
    async with client:
        assert await client.get_auth_tools() == []


@pytest.mark.asyncio
async def test_allowlist_hides_other_server_tools() -> None:
    other = Tool(name="create_issue", description="Create", inputSchema={})
    authorized = FakeClient(
        requires_user_action=False, tools=[LIST_ISSUES, other]
    )
    client = LangChainClient(authorized, tool_allowlist=["list_issues"])

    async with client:
        tools = await client.get_tools()

    assert [t.name for t in tools] == ["list_issues"]


@pytest.mark.asyncio
async def test_lazy_tools_report_the_servers_real_schema() -> None:
    """The reason lazy tools exist: the server's own parameters reach the model."""
    authorized = FakeClient(requires_user_action=False)
    client = LangChainClient(authorized)

    tools = await client.get_lazy_tools()
    listed = await next(t for t in tools if t.name == "list_mcp_tools").coroutine()

    assert '"state"' in listed and '"team"' in listed


@pytest.mark.asyncio
async def test_lazy_tools_interrupt_before_connecting(
    pending_client: FakeClient,
) -> None:
    client = LangChainClient(pending_client, interrupt_on_auth=True)
    tools = await client.get_lazy_tools()
    call_mcp_tool = next(t for t in tools if t.name == "call_mcp_tool")

    result = await run_tool_in_graph(
        call_mcp_tool, {"tool_name": "list_issues", "arguments": {"state": "open"}}
    )

    (paused,) = result["__interrupt__"]
    assert paused.value["type"] == "authorization_required"
    assert paused.value["authorization_url"] == AUTH_URL


@pytest.mark.asyncio
async def test_lazy_call_passes_arguments_to_the_server_tool() -> None:
    authorized = FakeClient(requires_user_action=False)
    client = LangChainClient(authorized)
    tools = await client.get_lazy_tools()
    call_mcp_tool = next(t for t in tools if t.name == "call_mcp_tool")

    result = await call_mcp_tool.coroutine(
        tool_name="list_issues", arguments={"state": "in progress"}
    )

    assert result == "ok"
    assert authorized.calls == [("list_issues", {"state": "in progress"})]
