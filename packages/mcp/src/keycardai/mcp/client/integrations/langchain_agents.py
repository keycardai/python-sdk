"""
LangChain agents adapter for KeycardAI MCP client.

Provides a clean API for integrating MCP tools with LangChain agents:
- Automatic auth detection and handling
- System prompt generation with auth context
- MCP tools converted to LangChain tools
- Auth request tools for agent
- Optional interrupt mode, so an MCP auth challenge pauses the run with the
  same `authorization_required` payload keycardai-langchain's
  KeycardGrantMiddleware raises
- Lazy tools for agents built before any user has connected
"""

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from ..client import Client
from ..types import AuthChallenge
from .auth_tools import AuthToolHandler, DefaultAuthToolHandler

logger = logging.getLogger(__name__)

AUTHORIZATION_REQUIRED = "authorization_required"

_AUTHORIZATION_MESSAGE = (
    "Access to the resources above has not been granted yet. "
    "Open the authorization URL to grant it, then resume the run."
)


def build_authorization_interrupt_payload(
    challenges: Sequence[AuthChallenge],
) -> dict[str, Any]:
    """The `authorization_required` interrupt payload for pending MCP challenges.

    Deliberately the same shape keycardai-langchain's KeycardGrantMiddleware
    produces for a missing grant, so an agent that combines brokered REST tools
    (middleware) with MCP tools (this adapter) hands its chat surface one
    payload to render, not two. MCP servers take the place of resource URLs in
    the `resources` and `errors` fields.

    Args:
        challenges: Pending challenges, as returned by
            `Client.get_auth_challenges()`.

    Returns:
        Interrupt payload with `type`, `resources`, `authorization_url`,
        `errors` and `message`.
    """
    servers = [challenge["server"] for challenge in challenges]
    authorization_url = next(
        (
            challenge["authorization_url"]
            for challenge in challenges
            if challenge.get("authorization_url")
        ),
        None,
    )
    return {
        "type": AUTHORIZATION_REQUIRED,
        "resources": servers,
        "authorization_url": authorization_url,
        "errors": {
            challenge["server"]: {
                "code": AUTHORIZATION_REQUIRED,
                "message": (
                    f"MCP server '{challenge['server']}' has not been authorized yet."
                ),
            }
            for challenge in challenges
        },
        "message": _AUTHORIZATION_MESSAGE,
    }


def _interrupt(payload: dict[str, Any]) -> None:
    """Raise a LangGraph interrupt.

    Imported lazily: langgraph is only needed by callers that opt into
    interrupt mode, and the adapter itself depends on langchain-core alone.
    """
    try:
        from langgraph.types import interrupt
    except ImportError as e:
        raise RuntimeError(
            "interrupt_on_auth requires langgraph. Install it with "
            "`uv add langgraph` (it ships with langchain 1.x agents)."
        ) from e
    interrupt(payload)


class LangChainClient:
    """
    LangChain agents adapter for MCP client.

    Wraps MCP client to provide:
    - get_system_prompt(): Instructions with auth awareness
    - get_tools(): MCP tools converted to LangChain tools
    - get_auth_tools(): Tools for requesting authentication

    Usage:
        async with langchain_agents.get_client(mcp_client) as client:
            agent = create_agent(
                model="claude-sonnet-4-5-20250929",
                tools=client.get_tools() + client.get_auth_tools(),
                system_prompt=client.get_system_prompt("Be helpful"),
            )
    """

    def __init__(
        self,
        mcp_client: Client,
        auth_tool_handler: AuthToolHandler | None = None,
        auth_hook_closure: Callable[[], Awaitable[None]] | None = None,
        auth_prompt: str | None = None,
        interrupt_on_auth: bool = False,
        tool_allowlist: Sequence[str] | None = None,
    ):
        """
        Initialize adapter.

        Args:
            mcp_client: KeycardAI MCP client
            auth_tool_handler: Optional custom handler for auth requests.
                If not provided, uses DefaultAuthToolHandler which returns
                auth messages for the agent to display.
                For custom flows (Slack, email, etc.), provide your own handler.
            auth_hook_closure: Optional async function called when auth is needed
            auth_prompt: Optional custom authentication prompt to include in system message
            interrupt_on_auth: Opt in to interrupt mode. A tool that hits an
                auth challenge pauses the run with the same
                `authorization_required` interrupt keycardai-langchain's
                KeycardGrantMiddleware raises, instead of handing the model
                auth-request tools. Off by default: the auth-tools behavior is
                unchanged unless this is set. Requires langgraph and a
                checkpointer on the agent.
            tool_allowlist: Optional server tool names to expose. Everything
                else the server advertises is hidden, which keeps a large
                server (say 67 tools) from flooding the model's context.
        """
        self._mcp_client = mcp_client
        self._auth_tool_handler = auth_tool_handler or DefaultAuthToolHandler()
        self._pending_challenges: list[dict[str, Any]] = []
        self._authenticated_servers: list[str] = []
        self._auth_hook_closure = auth_hook_closure
        self._tools_cache: list[StructuredTool] = []
        self.auth_prompt = auth_prompt
        self._interrupt_on_auth = interrupt_on_auth
        self._tool_allowlist = list(tool_allowlist) if tool_allowlist else None

    async def __aenter__(self) -> "LangChainClient":
        """
        Connect and analyze auth status.

        Returns:
            Self for use in async with statement
        """
        await self._mcp_client.connect()

        self._pending_challenges = await self._mcp_client.get_auth_challenges()

        if self._pending_challenges and self._auth_hook_closure:
            try:
                await self._auth_hook_closure()
            except Exception as e:
                logger.error(f"Error in auth hook closure: {e}", exc_info=True)

        try:
            tool_infos = await self._mcp_client.list_tools()
            # Group tools by server to determine which servers are authenticated
            servers_with_tools = {info.server for info in tool_infos}
            self._authenticated_servers = list(servers_with_tools)
        except (AttributeError, Exception) as e:
            # Session not fully connected (likely pending auth)
            # No authenticated servers available yet
            logger.error(f"Error listing tools: {e}", exc_info=True)
            self._authenticated_servers = []

        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Exit context manager."""
        # MCP client manages its own lifecycle
        pass

    def get_system_prompt(self, base_instructions: str) -> str:
        """
        Generate system prompt with auth awareness.

        If services need auth, adds instructions about using auth tool.
        Otherwise, returns base instructions unchanged.

        Args:
            base_instructions: Your base agent instructions

        Returns:
            System prompt (possibly augmented with auth instructions)

        Example:
            >>> prompt = client.get_system_prompt("You are a helpful assistant")
            >>> # If auth needed:
            >>> # "You are a helpful assistant\n\n**AUTH REQUIRED**..."
            >>> # If authenticated:
            >>> # "You are a helpful assistant"
        """
        if not self._pending_challenges:
            return base_instructions

        pending_services = [c["server"] for c in self._pending_challenges]

        auth_section = self.auth_prompt or f"""
**AUTHENTICATION STATUS:**
The following services require user authorization: {', '.join(pending_services)}
**IMPORTANT:** When the user requests an action that requires one of these services:
1. Call the `request_authentication` tool with:
   - service: The service name (e.g., "{pending_services[0]}")
   - reason: Brief explanation (e.g., "To access your calendar")
2. The tool will initiate the authorization flow and send the auth link to the user
3. Inform the user that you've initiated authorization and they should check for the link
4. After the user authorizes, you will automatically gain access to use that service
**Note:** You already have access to: {', '.join(self._authenticated_servers) if self._authenticated_servers else 'no services yet'}
"""

        return base_instructions + auth_section

    async def get_tools(self) -> list[StructuredTool]:
        """
        Get MCP tools converted to LangChain tools.

        Only returns tools from servers that are authenticated.
        Servers requiring auth are excluded until authorization completes.

        Returns:
            List of LangChain StructuredTool objects

        Example:
            >>> tools = await client.get_tools()
            >>> # If slack authenticated but gmail not:
            >>> # Returns tools from slack only
            >>> # Gmail tools excluded until user authorizes
        """
        # TODO: review the use of cache
        if self._tools_cache:
            return self._tools_cache

        tools = []
        for server_name in self._authenticated_servers:
            try:
                tool_infos = await self._mcp_client.list_tools(server_name)

                for tool_info in tool_infos:
                    if not self._is_allowed(tool_info.tool.name):
                        continue
                    langchain_tool = self._convert_mcp_tool_to_langchain(
                        tool_info.tool, tool_info.server
                    )
                    tools.append(langchain_tool)

            except Exception as e:
                logger.error(
                    f"Failed to load tools from server {server_name}: {e}",
                    exc_info=True,
                )
                continue

        self._tools_cache = tools
        return tools

    def _convert_mcp_tool_to_langchain(
        self, mcp_tool: Any, server_name: str
    ) -> StructuredTool:
        """
        Convert an MCP tool to a LangChain StructuredTool.

        Args:
            mcp_tool: MCP Tool object
            server_name: Name of the server this tool belongs to

        Returns:
            LangChain StructuredTool
        """
        tool_name = mcp_tool.name
        tool_description = mcp_tool.description or f"Tool {tool_name} from {server_name}"

        input_schema = mcp_tool.input_schema if hasattr(mcp_tool, "input_schema") else {}

        async def invoke_tool(**kwargs) -> str:
            """Invoke the MCP tool."""
            if self._interrupt_on_auth:
                await self._interrupt_if_authorization_required()
            try:
                result = await self._mcp_client.call_tool(
                    tool_name, kwargs, server_name=server_name
                )

                if isinstance(result, dict):
                    if "content" in result:
                        texts = []
                        for item in result.get("content", []):
                            if isinstance(item, dict):
                                if item.get("type") == "text":
                                    texts.append(item.get("text", ""))
                                else:
                                    texts.append(str(item))
                            else:
                                if hasattr(item, "text"):
                                    texts.append(item.text)
                                else:
                                    texts.append(str(item))
                        return "\n".join(texts) if texts else ""
                    else:
                        return json.dumps(result, indent=2)
                elif isinstance(result, str):
                    return result
                else:
                    return str(result)

            except Exception as e:
                logger.error(
                    f"Error calling tool {tool_name} on {server_name}: {e}",
                    exc_info=True,
                )
                return f"Error: {str(e)}"

        # Create the LangChain tool
        # Use the input schema from MCP tool if available
        if input_schema and isinstance(input_schema, dict):
            # LangChain expects 'properties' and 'required' keys
            tool = StructuredTool(
                name=tool_name,
                description=tool_description,
                coroutine=invoke_tool,
                args_schema=self._schema_to_pydantic(input_schema, tool_name),
            )
        else:
            tool = StructuredTool.from_function(
                name=tool_name,
                description=tool_description,
                coroutine=invoke_tool,
            )

        return tool

    def _schema_to_pydantic(self, json_schema: dict, tool_name: str) -> type:
        """
        Convert JSON schema to Pydantic model for LangChain.

        Args:
            json_schema: JSON schema dict
            tool_name: Name of the tool (for model naming)

        Returns:
            Pydantic model class
        """
        properties = json_schema.get("properties", {})
        required = json_schema.get("required", [])

        field_definitions = {}
        for field_name, field_info in properties.items():
            field_type = self._json_type_to_python(field_info.get("type", "string"))
            field_description = field_info.get("description", "")

            if field_name in required:
                field_definitions[field_name] = (
                    field_type,
                    Field(..., description=field_description),
                )
            else:
                field_definitions[field_name] = (
                    field_type | None,
                    Field(None, description=field_description),
                )

        model_name = f"{tool_name.replace('-', '_').replace(' ', '_')}_Input"
        return create_model(model_name, **field_definitions)

    def _json_type_to_python(self, json_type: str) -> type:
        """
        Convert JSON schema type to Python type.

        Args:
            json_type: JSON schema type string

        Returns:
            Python type
        """
        type_map = {
            "string": str,
            "number": float,
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        return type_map.get(json_type, str)

    def _is_allowed(self, tool_name: str) -> bool:
        """Whether a server tool passes the configured allowlist."""
        return self._tool_allowlist is None or tool_name in self._tool_allowlist

    async def _connect_and_refresh(self) -> None:
        """Connect the underlying client and re-read auth state.

        Lazy tools call this on invocation: the client for a given user may
        only be built once that user shows up, long after the agent's tool
        list was assembled.
        """
        await self._mcp_client.connect()
        self._pending_challenges = await self._mcp_client.get_auth_challenges()
        try:
            tool_infos = await self._mcp_client.list_tools()
            self._authenticated_servers = list({info.server for info in tool_infos})
        except Exception as e:
            logger.error(f"Error listing tools: {e}", exc_info=True)
            self._authenticated_servers = []
        self._tools_cache = []

    async def _pending_authorization_challenges(self) -> list[AuthChallenge]:
        """Challenges for sessions that are waiting on the user, if any.

        `session.requires_user_action` is the authoritative signal (status
        AUTH_PENDING); the challenge carries the `authorization_url` to show.
        """
        waiting = [
            name
            for name, session in self._mcp_client.sessions.items()
            if session.requires_user_action
        ]
        if not waiting:
            return []
        challenges = await self._mcp_client.get_auth_challenges()
        return [c for c in challenges if c["server"] in waiting]

    async def _interrupt_if_authorization_required(self) -> None:
        """Pause the run when a connected session is waiting on authorization."""
        challenges = await self._pending_authorization_challenges()
        if not challenges:
            return
        self._pending_challenges = list(challenges)
        _interrupt(build_authorization_interrupt_payload(challenges))

    async def get_lazy_tools(self) -> list[StructuredTool]:
        """
        Tools that can be bound before any user has connected.

        `create_agent` fixes its tool list when the module loads, but an MCP
        server's real tools are only knowable once a user has a connected
        session. These tools connect on first call and then reflect the
        server's actual schemas:

        - `list_mcp_tools` returns the server's tools with their full input
          schemas (filtered by `tool_allowlist`), so the model calls them with
          the server's own parameters rather than a hand-written subset.
        - `call_mcp_tool` invokes one of them by name.

        Both connect the client on invocation. In interrupt mode a pending
        challenge pauses the run; otherwise the auth message is returned to
        the model. Once a session is authorized, `get_tools()` returns the
        server's tools as first-class LangChain tools, which is the better
        binding whenever the agent can be built after `connect()`.

        Returns:
            List of lazy LangChain tools
        """

        def _allowed_infos(tool_infos):
            return [i for i in tool_infos if self._is_allowed(i.tool.name)]

        async def _prepare() -> str | None:
            """Connect, and report why tools are unavailable when they are."""
            await self._connect_and_refresh()
            if self._interrupt_on_auth:
                await self._interrupt_if_authorization_required()
                return None
            challenges = await self._pending_authorization_challenges()
            if not challenges:
                return None
            url = challenges[0].get("authorization_url")
            return (
                f"Authorization required for {challenges[0]['server']}. "
                f"Ask the user to visit: {url}"
            )

        async def list_mcp_tools() -> str:
            pending = await _prepare()
            if pending:
                return pending
            infos = _allowed_infos(await self._mcp_client.list_tools())
            return json.dumps(
                [
                    {
                        "name": info.tool.name,
                        "server": info.server,
                        "description": info.tool.description,
                        "input_schema": getattr(info.tool, "input_schema", {}),
                    }
                    for info in infos
                ],
                indent=2,
            )

        async def call_mcp_tool(tool_name: str, arguments: dict | None = None) -> str:
            pending = await _prepare()
            if pending:
                return pending
            if not self._is_allowed(tool_name):
                return f"Tool '{tool_name}' is not available."
            infos = _allowed_infos(await self._mcp_client.list_tools())
            info = next((i for i in infos if i.tool.name == tool_name), None)
            if info is None:
                available = ", ".join(i.tool.name for i in infos)
                return f"Tool '{tool_name}' not found. Available tools: {available}"
            tool = self._convert_mcp_tool_to_langchain(info.tool, info.server)
            return await tool.coroutine(**(arguments or {}))

        allowlist_note = (
            f" Available tools: {', '.join(self._tool_allowlist)}."
            if self._tool_allowlist
            else ""
        )

        class CallMcpToolInput(BaseModel):
            """Input for call_mcp_tool."""

            tool_name: str = Field(
                description="Name of the MCP tool, as returned by list_mcp_tools"
            )
            arguments: dict = Field(
                default_factory=dict,
                description=(
                    "Arguments for the tool, matching the input_schema "
                    "list_mcp_tools reported for it"
                ),
            )

        return [
            StructuredTool.from_function(
                name="list_mcp_tools",
                description=(
                    "List the MCP tools available to this user, with the input "
                    "schema of each. Call this before call_mcp_tool so the tool "
                    "is called with the server's own parameters." + allowlist_note
                ),
                coroutine=list_mcp_tools,
            ),
            StructuredTool(
                name="call_mcp_tool",
                description=(
                    "Call an MCP tool by name with the arguments its input "
                    "schema declares." + allowlist_note
                ),
                coroutine=call_mcp_tool,
                args_schema=CallMcpToolInput,
            ),
        ]

    async def get_auth_tools(self) -> list[StructuredTool]:
        """
        Get authentication request tools for the agent.

        Returns a tool that allows the agent to request user authentication
        when needed. If all services are authenticated, returns empty list.

        In interrupt mode there is no auth tool: the run pauses with an
        `authorization_required` interrupt instead of asking the model to
        request authorization, so this returns an empty list.

        Returns:
            List with one auth request tool (or empty if no auth needed)

        Example:
            >>> tools = await client.get_auth_tools()
            >>> # If auth needed:
            >>> # [StructuredTool(name="request_authentication", ...)]
            >>> # If all authenticated:
            >>> # []
        """
        if self._interrupt_on_auth or not self._pending_challenges:
            return []

        pending_services = [c["server"] for c in self._pending_challenges]

        async def request_authentication(service: str, reason: str) -> str:
            """
            Request user authentication for a service.

            Args:
                service: Service name (e.g., "slack", "gmail")
                reason: User-friendly explanation of why auth is needed

            Returns:
                Status message indicating auth flow initiated
            """
            challenge = next(
                (c for c in self._pending_challenges if c["server"] == service),
                None,
            )

            if not challenge:
                return f"Service '{service}' is already authenticated or not configured."

            try:
                result = await self._auth_tool_handler.handle_auth_request(
                    service=service,
                    reason=reason,
                    challenge=challenge,
                )
                return result
            except Exception as e:
                logger.error(f"Handler error: {e}", exc_info=True)
                # Don't expose internal exception details to agent
                return "Failed to initiate authorization. Please try again or contact support."


        class RequestAuthInput(BaseModel):
            """Input for request_authentication tool."""

            service: str = Field(
                description=f"Service name. Available services: {', '.join(pending_services)}"
            )
            reason: str = Field(
                description="User-friendly explanation of why you need access (e.g., 'To send messages to Slack channels')"
            )

        tool = StructuredTool(
            name="request_authentication",
            description=f"Request user authentication for services that need it. Available services: {', '.join(pending_services)}. Call this when the user wants to use one of these services.",
            coroutine=request_authentication,
            args_schema=RequestAuthInput,
        )

        return [tool]


def create_client(
    mcp_client: Client,
    auth_tool_handler: AuthToolHandler | None = None,
    auth_hook_closure: Callable[[], Awaitable[None]] | None = None,
    interrupt_on_auth: bool = False,
    tool_allowlist: Sequence[str] | None = None,
) -> LangChainClient:
    """
    Get LangChain agents adapter for MCP client.

    Use as context manager for automatic lifecycle management.

    Args:
        mcp_client: KeycardAI MCP client
        auth_tool_handler: Optional custom handler for auth requests.
            Subclass AuthToolHandler to customize how auth links are sent.
            Built-in options: SlackAuthToolHandler, ConsoleAuthToolHandler
            Default: DefaultAuthToolHandler (returns message for agent)
        auth_hook_closure: Optional async function called when auth is needed
        interrupt_on_auth: Opt in to interrupt mode (see LangChainClient)
        tool_allowlist: Optional server tool names to expose

    Returns:
        LangChain client adapter

    Example - Basic usage with default handler:
        >>> from langchain.agents import create_agent
        >>> from keycardai.mcp.client.integrations import langchain_agents
        >>>
        >>> async with langchain_agents.get_client(mcp_client) as client:
        ...     agent = create_agent(
        ...         model="claude-sonnet-4-5-20250929",
        ...         tools=await client.get_tools() + await client.get_auth_tools(),
        ...         system_prompt=client.get_system_prompt("Be helpful"),
        ...     )
        ...     result = agent.invoke({"messages": [{"role": "user", "content": "Hi"}]})

    Example - Slack integration:
        >>> from keycardai.mcp.client.integrations.auth_tools import SlackAuthToolHandler
        >>>
        >>> handler = SlackAuthToolHandler(
        ...     slack_client=slack_client,
        ...     channel_id=channel_id,
        ...     thread_ts=thread_ts
        ... )
        >>> async with langchain_agents.get_client(mcp_client, auth_tool_handler=handler) as client:
        ...     # Auth links will be sent directly to Slack thread

    Example - Console/CLI:
        >>> from keycardai.mcp.client.integrations.auth_tools import ConsoleAuthToolHandler
        >>>
        >>> handler = ConsoleAuthToolHandler()
        >>> async with langchain_agents.get_client(mcp_client, auth_tool_handler=handler) as client:
        ...     # Auth links will be printed to console

    Example - With memory/checkpointing:
        >>> from langgraph.checkpoint.memory import InMemorySaver
        >>> from langchain.agents import create_agent
        >>>
        >>> async with langchain_agents.get_client(mcp_client) as client:
        ...     agent = create_agent(
        ...         model="claude-sonnet-4-5-20250929",
        ...         tools=await client.get_tools() + await client.get_auth_tools(),
        ...         system_prompt=client.get_system_prompt("Be helpful"),
        ...         checkpointer=InMemorySaver(),
        ...     )
        ...     # Use with thread_id for conversation memory
        ...     result = agent.invoke(
        ...         {"messages": [{"role": "user", "content": "Hi, my name is Bob"}]},
        ...         {"configurable": {"thread_id": "123"}},
        ...     )

    Example - Interrupt mode alongside keycardai-langchain's middleware:
        >>> client = langchain_agents.create_client(
        ...     mcp_client,
        ...     interrupt_on_auth=True,
        ...     tool_allowlist=["list_issues", "create_issue"],
        ... )
        >>> # A pending MCP challenge now pauses the run with the same
        >>> # `authorization_required` payload KeycardGrantMiddleware raises.
    """
    return LangChainClient(
        mcp_client,
        auth_tool_handler,
        auth_hook_closure,
        interrupt_on_auth=interrupt_on_auth,
        tool_allowlist=tool_allowlist,
    )

