"""
OpenAI agents SDK adapter for KeycardAI MCP client.

Provides a clean API for integrating MCP tools with OpenAI agents:
- Automatic auth detection and handling
- System prompt generation with auth context
- Filtered MCP servers (only authenticated)
- Auth request tools for agent
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agents import FunctionTool
from mcp import Tool
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    ListPromptsResult,
    TextContent,
)

from ..client import Client
from ..exceptions import MCPClientError
from .auth_tools import AuthToolHandler, DefaultAuthToolHandler

logger = logging.getLogger(__name__)

# Type alias for tool result parser
ToolResultParser = Callable[[Any], CallToolResult]


class OpenAIMCPServer:
    """
    OpenAI-compatible wrapper for an MCP server.

    This is what gets passed to Agent(mcp_servers=[...])

    Wraps a single MCP server to provide the interface expected by
    OpenAI agents SDK for MCP server integration.

    Implements the MCPServer protocol from agents.mcp.
    """

    def __init__(
        self,
        client: Client,
        server_name: str,
        use_structured_content: bool = True,
        tool_result_parser: ToolResultParser | None = None,
    ):
        """
        Initialize OpenAI MCP server adapter.

        Args:
            client: KeycardAI MCP client
            server_name: Name of the server to wrap
            use_structured_content: Whether to use structured content in tool results
            tool_result_parser: Optional custom parser for tool results.
                If not provided, uses the default parser.
        """
        self._client = client
        self._server_name = server_name
        self._tool_result_parser = tool_result_parser

        self.use_structured_content = use_structured_content

    @property
    def name(self) -> str:
        """A readable name for the server."""
        return self._server_name

    async def connect(self):
        """
        Connect to the server.

        Note: Connection is managed by the MCP client, so this is a no-op.
        The client should already be connected before creating this adapter.
        """
        pass

    async def cleanup(self):
        """
        Cleanup the server.

        Note: Cleanup is managed by the MCP client, so this is a no-op.
        """
        pass

    def _default_result_parser(self, result: Any) -> CallToolResult:
        """
        Default parser for tool results.

        Converts various result types into CallToolResult format expected by OpenAI agents.

        Required based on real work usage

        Args:
            result: Raw result from MCP tool call

        Returns:
            CallToolResult with properly formatted content
        """
        # Handle various primitive types
        if result is None:
            text = ""
        elif isinstance(result, str):
            text = result
        elif isinstance(result, int | float | bool):
            text = str(result)
        elif isinstance(result, list | dict):
            try:
                text = json.dumps(result, indent=2)
            except Exception:
                text = str(result)
        else:
            text = str(result)

        call_tool_result = CallToolResult(
            content=[TextContent(type="text", text=text)],
            isError=False,
        )

        logger.debug(f"Returning CallToolResult with {len(call_tool_result.content)} content items")
        return call_tool_result

        # TODO: Delete the claude generated parser if no obvious reasons
        """
        # If already a CallToolResult, return as-is
        if isinstance(result, CallToolResult):
            return result

        # Handle dict with content/isError structure
        if isinstance(result, dict):
            if "content" in result or "isError" in result:
                content_items = []
                for item in result.get("content", []):
                    if isinstance(item, dict):
                        item_type = item.get("type", "text")
                        if item_type == "text":
                            content_items.append(TextContent(type="text", text=item.get("text", "")))
                        elif item_type == "image":
                            content_items.append(ImageContent(
                                type="image",
                                data=item.get("data", ""),
                                mimeType=item.get("mimeType", "image/png")
                            ))
                        elif item_type == "resource":
                            content_items.append(EmbeddedResource(
                                type="resource",
                                resource=item.get("resource", {})
                            ))
                        else:
                            content_items.append(TextContent(type="text", text=str(item)))
                    else:
                        content_items.append(item)

                return CallToolResult(
                    content=content_items if content_items else [TextContent(type="text", text=str(result))],
                    isError=result.get("isError", False),
                )

        """

    async def list_tools(
        self,
        run_context: Any | None = None,
        agent: Any | None = None,
    ) -> list[Tool]:
        """
        List the tools available on the server.

        Args:
            run_context: Optional run context (not used in this implementation)
            agent: Optional agent reference (not used in this implementation)

        Returns:
            List of tools available on this server
        """
        if not self._client:
            return []
        tool_infos = await self._client.list_tools(self._server_name)
        return [info.tool for info in tool_infos]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        """
        Invoke a tool on the server.

        Args:
            tool_name: Name of the tool to call
            arguments: Arguments to pass to the tool (can be None)

        Returns:
            Result from the tool call wrapped in CallToolResult
        """
        if arguments is None:
            arguments = {}

        result = await self._client.call_tool(
            tool_name,
            arguments,
            server_name=self._server_name
        )

        parser = self._tool_result_parser or self._default_result_parser
        return parser(result)

    async def list_prompts(self) -> ListPromptsResult:
        """
        List the prompts available on the server.

        Note: Not implemented - MCP client doesn't expose prompt listing yet.
        Returns empty list.
        """
        return ListPromptsResult(prompts=[])

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        """
        Get a specific prompt from the server.

        Note: Not implemented - MCP client doesn't expose prompts yet.
        Raises UserError.
        """
        raise MCPClientError(f"Prompts are not supported. Requested prompt: {name}")


class OpenAIAgentsClient:
    """
    OpenAI agents adapter for MCP client.

    Wraps MCP client to provide:
    - get_system_prompt(): Instructions with auth awareness
    - get_mcp_servers(): Filtered authenticated servers
    - get_auth_tools(): Tools for requesting authentication

    Usage:
        async with openai_agents.get_client(mcp_client) as client:
            agent = Agent(
                instructions=client.get_system_prompt("base instructions"),
                mcp_servers=client.get_mcp_servers(),
                tools=client.get_auth_tools(),
            )
    """

    def __init__(
        self,
        mcp_client: Client,
        auth_tool_handler: AuthToolHandler | None = None,
        auth_hook_closure: Callable[[], Awaitable[None]] | None = None,
        auth_prompt: str | None = None,
        tool_result_parser: Callable[[Any], CallToolResult] | None = None,
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
            tool_result_parser: Optional custom parser for tool results. If not provided,
                uses the default parser that handles primitive types and JSON serialization.
        """
        self._mcp_client = mcp_client
        self._auth_tool_handler = auth_tool_handler or DefaultAuthToolHandler()
        self._pending_challenges: list[dict[str, Any]] = []
        self._authenticated_servers: list[str] = []
        self._auth_hook_closure = auth_hook_closure
        self.auth_prompt = auth_prompt
        self.tool_result_parser = tool_result_parser
    async def __aenter__(self) -> "OpenAIAgentsClient":
        """
        Connect and analyze auth status.

        Returns:
            Self for use in async with statement
        """
        await self._mcp_client.connect()

        self._pending_challenges = await self._mcp_client.get_auth_challenges()

        if self._pending_challenges:
            try:
                await self._auth_hook_closure()
            except Exception as e:
                logger.error(f"Error in auth hook closure: {e}", exc_info=True)


        # TODO: test this full and refactor. This should not have to raise!
        # Note: This may fail if sessions aren't fully connected due to pending auth
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
            >>> prompt = client.get_system_prompt("You are a helpful Slack bot")
            >>> # If auth needed:
            >>> # "You are a helpful Slack bot\n\n**AUTH REQUIRED**..."
            >>> # If authenticated:
            >>> # "You are a helpful Slack bot"
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

    def get_mcp_servers(self) -> list[OpenAIMCPServer]:
        """
        Get MCP servers for OpenAI agent.

        Only returns servers that are authenticated and have tools available.
        Servers requiring auth are excluded until authorization completes.

        Returns:
            List of MCP server objects for OpenAI Agent

        Example:
            >>> servers = client.get_mcp_servers()
            >>> # If slack authenticated but gmail not:
            >>> # Returns adapters for slack only
            >>> # Gmail excluded until user authorizes
        """
        servers = []
        for server_name in self._authenticated_servers:
            try:
                server = OpenAIMCPServer(
                    client=self._mcp_client,
                    server_name=server_name,
                    tool_result_parser=self.tool_result_parser,
                )
                servers.append(server)
            except Exception:
                continue
        return servers

    def get_auth_tools(self) -> list[FunctionTool]:
        """
        Get authentication request tools for the agent.

        Returns a tool that allows the agent to request user authentication
        when needed. If all services are authenticated, returns empty list.

        Returns:
            List with one auth request tool (or empty if no auth needed)

        Example:
            >>> tools = client.get_auth_tools()
            >>> # If auth needed:
            >>> # [FunctionTool(name="request_authentication", ...)]
            >>> # If all authenticated:
            >>> # []
        """
        if not self._pending_challenges:
            return []

        pending_services = [c["server"] for c in self._pending_challenges]

        params_json_schema = {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": f"Service name. Available services: {', '.join(pending_services)}",
                    "enum": pending_services,
                },
                "reason": {
                    "type": "string",
                    "description": "User-friendly explanation of why you need access (e.g., 'To send messages to Slack channels')",
                },
            },
            "required": ["service", "reason"],
            "additionalProperties": False,
        }

        async def on_invoke_tool(ctx: Any, arguments_json: str) -> str:
            """
            Handle the tool invocation.

            Args:
                ctx: Tool context (not used in this implementation)
                arguments_json: JSON string with service and reason

            Returns:
                Status message for the agent (not shown to user)
            """
            try:
                args = json.loads(arguments_json)
                service = args.get("service")
                reason = args.get("reason", "")
            except (json.JSONDecodeError, KeyError) as e:
                return f"Invalid arguments: {e}"

            challenge = next(
                (c for c in self._pending_challenges if c["server"] == service),
                None
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

        tool = FunctionTool(
            name="request_authentication",
            description=f"Request user authentication for services that need it. Available services: {', '.join(pending_services)}. Call this when the user wants to use one of these services.",
            params_json_schema=params_json_schema,
            on_invoke_tool=on_invoke_tool,
            strict_json_schema=True,
        )

        return [tool]


def create_client(
    mcp_client: Client,
    auth_tool_handler: AuthToolHandler | None = None,
    auth_hook_closure: Callable[[], Awaitable[None]] | None = None,
    tool_result_parser: Callable[[Any], CallToolResult] | None = None,
) -> OpenAIAgentsClient:
    """
    Get OpenAI agents adapter for MCP client.

    Use as context manager for automatic lifecycle management.

    Args:
        mcp_client: KeycardAI MCP client
        auth_tool_handler: Optional custom handler for auth requests.
            Subclass AuthToolHandler to customize how auth links are sent.
            Built-in options: SlackAuthToolHandler, ConsoleAuthToolHandler
            Default: DefaultAuthToolHandler (returns message for agent)
        auth_hook_closure: Optional async function called when auth is needed

    Returns:
        OpenAI agents client adapter

    Example - Basic usage with default handler:
        >>> async with get_client(mcp_client) as client:
        ...     agent = Agent(
        ...         instructions=client.get_system_prompt("Be helpful"),
        ...         mcp_servers=client.get_mcp_servers(),
        ...         tools=client.get_auth_tools(),
        ...     )
        ...     result = await Runner.run(agent, user_message)

    Example - Slack integration:
        >>> from keycardai.mcp.client.integrations.auth_tools import SlackAuthToolHandler
        >>>
        >>> handler = SlackAuthToolHandler(
        ...     slack_client=slack_client,
        ...     channel_id=channel_id,
        ...     thread_ts=thread_ts
        ... )
        >>> async with get_client(mcp_client, auth_tool_handler=handler) as client:
        ...     # Auth links will be sent directly to Slack thread

    Example - Console/CLI:
        >>> from keycardai.mcp.client.integrations.auth_tools import ConsoleAuthToolHandler
        >>>
        >>> handler = ConsoleAuthToolHandler()
        >>> async with get_client(mcp_client, auth_tool_handler=handler) as client:
        ...     # Auth links will be printed to console
    """
    return OpenAIAgentsClient(mcp_client, auth_tool_handler, auth_hook_closure, tool_result_parser)
