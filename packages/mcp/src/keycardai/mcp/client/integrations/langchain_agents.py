"""
LangChain agents adapter for KeycardAI MCP client.

Provides a clean API for integrating MCP tools with LangChain agents:
- Automatic auth detection and handling
- System prompt generation with auth context
- MCP tools converted to LangChain tools
- Auth request tools for agent
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from ..client import Client
from .auth_tools import AuthToolHandler, DefaultAuthToolHandler

logger = logging.getLogger(__name__)


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
        """
        self._mcp_client = mcp_client
        self._auth_tool_handler = auth_tool_handler or DefaultAuthToolHandler()
        self._pending_challenges: list[dict[str, Any]] = []
        self._authenticated_servers: list[str] = []
        self._auth_hook_closure = auth_hook_closure
        self._tools_cache: list[StructuredTool] = []
        self.auth_prompt = auth_prompt

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

        input_schema = mcp_tool.inputSchema if hasattr(mcp_tool, "inputSchema") else {}

        async def invoke_tool(**kwargs) -> str:
            """Invoke the MCP tool."""
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

    async def get_auth_tools(self) -> list[StructuredTool]:
        """
        Get authentication request tools for the agent.

        Returns a tool that allows the agent to request user authentication
        when needed. If all services are authenticated, returns empty list.

        Returns:
            List with one auth request tool (or empty if no auth needed)

        Example:
            >>> tools = await client.get_auth_tools()
            >>> # If auth needed:
            >>> # [StructuredTool(name="request_authentication", ...)]
            >>> # If all authenticated:
            >>> # []
        """
        if not self._pending_challenges:
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
    """
    return LangChainClient(mcp_client, auth_tool_handler, auth_hook_closure)

