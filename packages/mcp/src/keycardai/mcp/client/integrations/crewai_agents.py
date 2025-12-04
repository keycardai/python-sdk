"""
CrewAI agents adapter for KeycardAI MCP client.

Provides a clean API for integrating MCP tools with CrewAI agents:
- Automatic auth detection and handling
- System prompt generation with auth context
- MCP tools converted to CrewAI tools
- Auth request tools for agent

Usage:
    from keycardai.agents.crewai_agents import create_client
    from keycardai.mcp.client import Client as MCPClient

    async with create_client(mcp_client) as client:
        tools = await client.get_tools()
        auth_tools = await client.get_auth_tools()

        crew = Crew(
            agents=[...],
            tasks=[...],
            tools=tools + auth_tools,
        )
        result = crew.kickoff()
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

# Apply nest_asyncio to allow nested event loops
# This is needed because CrewAI calls tools synchronously but we may be
# in an async context (e.g., when using async MCP client)
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    # nest_asyncio not installed - will fail if nesting occurs
    pass

try:
    from crewai.tools import BaseTool
except ImportError:
    raise ImportError(
        "CrewAI is not installed. Install it with: pip install 'keycardai-mcp[crewai]'"
    ) from None

from keycardai.mcp.client import Client

logger = logging.getLogger(__name__)


class AuthToolHandler:
    """Base handler for authentication requests from agents."""

    async def handle_auth_request(
        self, service: str, reason: str, challenge: dict[str, Any]
    ) -> str:
        """Handle authentication request from agent.

        Args:
            service: Service name requiring authentication
            reason: User-friendly explanation of why auth is needed
            challenge: Auth challenge data from MCP server

        Returns:
            Status message for the agent
        """
        raise NotImplementedError


class DefaultAuthToolHandler(AuthToolHandler):
    """Default handler that returns auth messages for agent to display."""

    async def handle_auth_request(
        self, service: str, reason: str, challenge: dict[str, Any]
    ) -> str:
        """Return formatted auth message with link."""
        auth_url = challenge.get("auth_url", "")
        if auth_url:
            return f"""Authorization initiated for {service}.

Reason: {reason}

Please visit this URL to authorize:
{auth_url}

Once you've authorized, I will automatically gain access to {service}."""
        else:
            return f"Authorization required for {service}. Reason: {reason}"


class CrewAIClient:
    """
    CrewAI agents adapter for MCP client.

    Wraps MCP client to provide:
    - get_tools(): MCP tools converted to CrewAI tools
    - get_auth_tools(): Tools for requesting authentication
    - get_system_prompt(): Instructions with auth awareness

    Usage:
        async with create_client(mcp_client) as client:
            tools = await client.get_tools()
            auth_tools = await client.get_auth_tools()

            crew = Crew(
                agents=[
                    Agent(
                        role="GitHub Expert",
                        goal="Analyze pull requests",
                        tools=tools + auth_tools,
                    )
                ],
                tasks=[...],
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
            auth_hook_closure: Optional async function called when auth is needed
            auth_prompt: Optional custom authentication prompt to include in system message
        """
        self._mcp_client = mcp_client
        self._auth_tool_handler = auth_tool_handler or DefaultAuthToolHandler()
        self._pending_challenges: list[dict[str, Any]] = []
        self._authenticated_servers: list[str] = []
        self._auth_hook_closure = auth_hook_closure
        self._tools_cache: list[BaseTool] = []
        self.auth_prompt = auth_prompt

    async def __aenter__(self) -> "CrewAIClient":
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
        except Exception as e:
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
            >>> # "You are a helpful assistant\\n\\n**AUTH REQUIRED**..."
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
   - reason: Brief explanation (e.g., "To access your GitHub repositories")
2. The tool will initiate the authorization flow and send the auth link to the user
3. Inform the user that you've initiated authorization and they should check for the link
4. After the user authorizes, you will automatically gain access to use that service

**Note:** You already have access to: {', '.join(self._authenticated_servers) if self._authenticated_servers else 'no services yet'}
"""

        return base_instructions + auth_section

    async def get_tools(self) -> list[BaseTool]:
        """
        Get MCP tools converted to CrewAI tools.

        Only returns tools from servers that are authenticated.
        Servers requiring auth are excluded until authorization completes.

        Returns:
            List of CrewAI BaseTool objects

        Example:
            >>> tools = await client.get_tools()
            >>> # If GitHub authenticated but Slack not:
            >>> # Returns tools from GitHub only
            >>> # Slack tools excluded until user authorizes
        """
        # Return cached tools if available (cache is cleared on reconnect)
        if self._tools_cache:
            return self._tools_cache

        tools = []
        for server_name in self._authenticated_servers:
            try:
                tool_infos = await self._mcp_client.list_tools(server_name)

                for tool_info in tool_infos:
                    crewai_tool = self._convert_mcp_tool_to_crewai(
                        tool_info.tool, tool_info.server
                    )
                    tools.append(crewai_tool)

            except Exception as e:
                logger.error(
                    f"Failed to load tools from server {server_name}: {e}",
                    exc_info=True,
                )
                continue

        self._tools_cache = tools
        return tools

    def _convert_mcp_tool_to_crewai(
        self, mcp_tool: Any, server_name: str
    ) -> BaseTool:
        """
        Convert an MCP tool to a CrewAI BaseTool.

        Args:
            mcp_tool: MCP Tool object
            server_name: Name of the server this tool belongs to

        Returns:
            CrewAI BaseTool
        """
        tool_name = mcp_tool.name
        tool_description = mcp_tool.description or f"Tool {tool_name} from {server_name}"

        input_schema = mcp_tool.inputSchema if hasattr(mcp_tool, "inputSchema") else {}

        # Create args schema from MCP tool schema
        tool_args_schema = None
        if input_schema and isinstance(input_schema, dict):
            tool_args_schema = self._schema_to_pydantic(input_schema, tool_name)

        # Define common methods as closures
        def _run_impl(self, *args, **kwargs) -> str:
            """Synchronous run - wraps async execution for CrewAI compatibility."""
            return asyncio.run(_async_run_impl(self, *args, **kwargs))

        async def _async_run_impl(self, *args, **kwargs) -> str:
            """Invoke the MCP tool asynchronously."""
            # If args are passed (single dict argument from Pydantic model), convert to kwargs
            if args and len(args) == 1 and isinstance(args[0], dict):
                kwargs = args[0]

            try:
                result = await self._mcp_client.call_tool(
                    tool_name, kwargs, server_name=self._server_name
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
                    f"Error calling tool {tool_name} on {self._server_name}: {e}",
                    exc_info=True,
                )
                return f"Error: {str(e)}"

        # Create a dynamic tool class with proper annotations
        if tool_args_schema:
            class MCPToolWrapper(BaseTool):
                """Wrapper for MCP tool."""

                name: str = tool_name
                description: str = tool_description
                args_schema: type[BaseModel] = tool_args_schema

                def __init__(self, mcp_client: Client, server_name: str, **kwargs):
                    super().__init__(**kwargs)
                    self._mcp_client = mcp_client
                    self._server_name = server_name

                def invoke(self, input: str | dict, config: dict | None = None, **kwargs) -> str:
                    """Bridge CrewAI's invoke() convention to our _run() implementation."""
                    # Parse JSON string if needed
                    if isinstance(input, str):
                        try:
                            input = json.loads(input)
                        except json.JSONDecodeError as e:
                            error_msg = f"Error: Failed to parse arguments as JSON: {e}"
                            logger.error(f"[{tool_name}] {error_msg}")
                            return error_msg

                    # Validate using Pydantic args_schema
                    try:
                        validated_args = self.args_schema.model_validate(input)
                        arguments = validated_args.model_dump()
                    except Exception as e:
                        error_msg = f"Error: Arguments validation failed: {e}"
                        logger.error(f"[{tool_name}] {error_msg}")
                        return error_msg

                    # Call existing _run implementation
                    return self._run(**arguments)

                def _run(self, *args, **kwargs) -> str:
                    return _run_impl(self, *args, **kwargs)

                async def async_run(self, *args, **kwargs) -> str:
                    return await _async_run_impl(self, *args, **kwargs)
        else:
            class MCPToolWrapper(BaseTool):
                """Wrapper for MCP tool."""

                name: str = tool_name
                description: str = tool_description

                def __init__(self, mcp_client: Client, server_name: str, **kwargs):
                    super().__init__(**kwargs)
                    self._mcp_client = mcp_client
                    self._server_name = server_name

                def invoke(self, input: str | dict, config: dict | None = None, **kwargs) -> str:
                    """Bridge CrewAI's invoke() convention to our _run() implementation."""
                    # Parse JSON string if needed
                    if isinstance(input, str):
                        try:
                            input = json.loads(input)
                        except json.JSONDecodeError as e:
                            error_msg = f"Error: Failed to parse arguments as JSON: {e}"
                            logger.error(f"[{tool_name}] {error_msg}")
                            return error_msg

                    # No schema validation needed - pass directly
                    if isinstance(input, dict):
                        return self._run(**input)
                    else:
                        error_msg = f"Error: Expected dict or JSON string, got {type(input)}"
                        logger.error(f"[{tool_name}] {error_msg}")
                        return error_msg

                def _run(self, *args, **kwargs) -> str:
                    return _run_impl(self, *args, **kwargs)

                async def async_run(self, *args, **kwargs) -> str:
                    return await _async_run_impl(self, *args, **kwargs)

        # Instantiate the tool with MCP client reference
        tool_instance = MCPToolWrapper(
            mcp_client=self._mcp_client,
            server_name=server_name
        )

        return tool_instance

    def _schema_to_pydantic(self, json_schema: dict, tool_name: str) -> type[BaseModel]:
        """
        Convert JSON schema to Pydantic model for CrewAI.

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

        # Create dynamic Pydantic model
        return type(
            model_name,
            (BaseModel,),
            {
                "__annotations__": {k: v[0] for k, v in field_definitions.items()},
                **{k: v[1] for k, v in field_definitions.items()},
            },
        )

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

    async def get_auth_tools(self) -> list[BaseTool]:
        """
        Get authentication request tools for the agent.

        Returns a tool that allows the agent to request user authentication
        when needed. If all services are authenticated, returns empty list.

        Returns:
            List with one auth request tool (or empty if no auth needed)

        Example:
            >>> tools = await client.get_auth_tools()
            >>> # If auth needed:
            >>> # [BaseTool(name="request_authentication", ...)]
            >>> # If all authenticated:
            >>> # []
        """
        if not self._pending_challenges:
            return []

        pending_services = [c["server"] for c in self._pending_challenges]

        class RequestAuthenticationTool(BaseTool):
            """Tool for requesting user authentication."""

            name: str = "request_authentication"
            description: str = (
                f"Request user authentication for services that need it. "
                f"Available services: {', '.join(pending_services)}. "
                f"Call this when the user wants to use one of these services."
            )

            def __init__(
                self,
                auth_handler: AuthToolHandler,
                challenges: list[dict[str, Any]],
                **kwargs
            ):
                super().__init__(**kwargs)
                self._auth_handler = auth_handler
                self._challenges = challenges

            def _run(self, service: str, reason: str) -> str:
                """Synchronous run - not supported for async auth."""
                raise NotImplementedError(
                    "Authentication requires async execution. Use async_run() instead."
                )

            async def async_run(self, service: str, reason: str) -> str:
                """
                Request user authentication for a service.

                Args:
                    service: Service name (e.g., "github", "slack")
                    reason: User-friendly explanation of why auth is needed

                Returns:
                    Status message indicating auth flow initiated
                """
                challenge = next(
                    (c for c in self._challenges if c["server"] == service),
                    None,
                )

                if not challenge:
                    return f"Service '{service}' is already authenticated or not configured."

                try:
                    result = await self._auth_handler.handle_auth_request(
                        service=service,
                        reason=reason,
                        challenge=challenge,
                    )
                    return result
                except Exception as e:
                    logger.error(f"Handler error: {e}", exc_info=True)
                    # Don't expose internal exception details to agent
                    return "Failed to initiate authorization. Please try again or contact support."

        # Create args schema for the auth tool
        class RequestAuthInput(BaseModel):
            """Input for request_authentication tool."""

            service: str = Field(
                description=f"Service name. Available services: {', '.join(pending_services)}"
            )
            reason: str = Field(
                description="User-friendly explanation of why you need access (e.g., 'To analyze your GitHub pull requests')"
            )

        RequestAuthenticationTool.args_schema = RequestAuthInput

        tool = RequestAuthenticationTool(
            auth_handler=self._auth_tool_handler,
            challenges=self._pending_challenges,
        )

        return [tool]


def create_client(
    mcp_client: Client,
    auth_tool_handler: AuthToolHandler | None = None,
    auth_hook_closure: Callable[[], Awaitable[None]] | None = None,
) -> CrewAIClient:
    """
    Get CrewAI agents adapter for MCP client.

    Use as context manager for automatic lifecycle management.

    Args:
        mcp_client: KeycardAI MCP client
        auth_tool_handler: Optional custom handler for auth requests.
            Subclass AuthToolHandler to customize how auth links are sent.
            Default: DefaultAuthToolHandler (returns message for agent)
        auth_hook_closure: Optional async function called when auth is needed

    Returns:
        CrewAI client adapter

    Example - Basic usage:
        >>> from keycardai.agents.crewai_agents import create_client
        >>> from keycardai.mcp.client import Client as MCPClient
        >>> from crewai import Crew, Agent, Task
        >>>
        >>> async with create_client(mcp_client) as client:
        ...     tools = await client.get_tools()
        ...     auth_tools = await client.get_auth_tools()
        ...
        ...     agent = Agent(
        ...         role="GitHub Expert",
        ...         goal="Analyze pull requests",
        ...         backstory=client.get_system_prompt("You are a code review expert"),
        ...         tools=tools + auth_tools,
        ...     )
        ...
        ...     crew = Crew(agents=[agent], tasks=[...])
        ...     result = crew.kickoff()

    Example - With custom auth handler:
        >>> class SlackAuthHandler(AuthToolHandler):
        ...     async def handle_auth_request(self, service, reason, challenge):
        ...         # Send auth link to Slack channel
        ...         await slack_client.post_message(channel_id, challenge['auth_url'])
        ...         return f"Authorization link sent to Slack"
        >>>
        >>> handler = SlackAuthHandler()
        >>> async with create_client(mcp_client, auth_tool_handler=handler) as client:
        ...     # Auth links will be sent to Slack
    """
    return CrewAIClient(mcp_client, auth_tool_handler, auth_hook_closure)
