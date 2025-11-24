from typing import Any

from mcp import Tool
from mcp.types import CallToolResult, PaginatedRequestParams
from nanoid import generate

from .auth.coordinators import AuthCoordinator, LocalAuthCoordinator
from .context import Context
from .exceptions import ClientConfigurationError, ToolNotFoundException
from .logging_config import get_logger
from .session import Session
from .storage import InMemoryBackend, StorageBackend
from .types import AuthChallenge, ToolInfo

logger = get_logger(__name__)

class Client:
    """
    MCP client for connecting to servers.

    Usage:
        # Simple local usage:
        async with Client(servers) as client:
            result = await client.call_tool("server", "tool", {})

        # Or manual lifecycle:
        client = Client(servers)
        await client.connect()
        result = await client.call_tool("server", "tool", {})
        await client.disconnect()
    """

    def __init__(
        self,
        servers: dict[str, Any],
        context_id: str | None = None,
        storage_backend: StorageBackend | None = None,
        auth_coordinator: AuthCoordinator | None = None,
        context: Context | None = None,
        metadata: dict[str, Any] | None = None
    ):
        """
        Initialize MCP client.

        Args:
            servers: Server configurations
            context_id: Optional context identifier (auto-generated if not provided)
            storage_backend: Storage backend (defaults to InMemoryBackend)
            auth_coordinator: Optional coordinator (creates LocalAuthCoordinator if not provided)
            context: Optional pre-built context (for advanced usage via ClientManager)
            metadata: Optional metadata dict to attach to context (e.g., user info, session data)
        """
        if context and context_id:
            raise ClientConfigurationError(
                context_id=context_id,
                has_context=True
            )

        self._context_id = context_id or self._generate_client_id()

        if storage_backend is None:
            storage_backend = InMemoryBackend()

        self.auth_coordinator = auth_coordinator or LocalAuthCoordinator(
            backend=storage_backend
        )

        self.context = context or self.auth_coordinator.create_context(self._context_id, metadata=metadata)

        self.sessions: dict[str, Session] = {}
        for server_name, server_config in servers.items():
            self.add_server(server_name, server_config)

    def add_server(self, server_name: str, server_config: Any) -> None:
        """
        Add a server connection to this client.

        Args:
            server_name: Name of the server
            server_config: Server configuration (including URL and auth config)
        """
        self.sessions[server_name] = Session(
            server_name=server_name,
            server_config=server_config,
            context=self.context,
            coordinator=self.auth_coordinator
        )

    def _generate_client_id(self) -> str:
        return str(generate(size=16))

    async def __aenter__(self) -> "Client":
        """
        Async context manager entry.
        Connects to servers.
        """
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        """
        Async context manager exit.
        Disconnects from servers.
        """
        await self.disconnect()

    async def connect(self, server: str | None = None, force_reconnect: bool = False) -> None:
        """
        Connect to servers.

        This method attempts to connect to the specified server(s). Connection failures
        are communicated via session status, not exceptions. Check session.status or
        session.is_operational after calling to determine outcome.

        Args:
            server: Optional server name. If None, connects to all servers.
            force_reconnect: If True, disconnect and reconnect even if already connected.
        """
        sessions = [server] if server is not None else list(self.sessions.keys())
        for session_name in sessions:
            try:
                if force_reconnect:
                    await self.sessions[session_name].disconnect()
                await self.sessions[session_name].connect()
            except Exception:
                # Session.connect() should not raise for normal failures - this catches
                # only truly unexpected programmer errors
                logger.error(f"Unexpected error connecting to server {session_name}", exc_info=True)

    async def requires_auth(self, server_name: str | None = None) -> bool:
        sessions = [server_name] if server_name is not None else list(self.sessions.keys())
        for session in sessions:
            if await self.sessions[session].requires_auth():
                return True
        return False

    async def get_auth_challenges(self, server_name: str | None = None) -> list[AuthChallenge]:
        """
        Get all pending auth challenges across sessions.

        An auth challenge is created when a server requires authentication
        that hasn't been completed yet. The challenge details are strategy-specific.

        Args:
            server_name: Optional server name to check. If None, checks all sessions.

        Returns:
            List of auth challenges. Each AuthChallenge contains strategy-specific fields
            plus a 'server' field indicating which server needs auth.
            Empty list if no challenges pending.

        Example:
            For OAuth challenges:
            [
                {
                    'authorization_url': 'https://...',
                    'state': 'abc123',
                    'server': 'my-server'
                }
            ]
        """
        sessions_to_check = [server_name] if server_name else list(self.sessions.keys())
        auth_challenges: list[AuthChallenge] = []

        for session_name in sessions_to_check:
            session = self.sessions[session_name]
            challenge = await session.get_auth_challenge()
            if challenge:
                auth_challenges.append({
                    **challenge,
                    "server": session_name
                })

        return auth_challenges

    async def disconnect(self) -> None:
        for session in self.sessions.values():
            await session.disconnect()

    async def list_tools(self, server_name: str | None = None) -> list[ToolInfo]:
        """
        List all available tools with their server information.

        Each tool is returned with explicit information about which server provides it.
        This makes it easy to iterate over tools while knowing their provenance.

        Automatically handles pagination by fetching all pages.

        Args:
            server_name: Optional server name. If provided, lists tools only from that server.
                        If None, lists tools from all servers.

        Returns:
            List of ToolInfo objects, each containing a tool and its server name.

        Example:
            >>> tools = await client.list_tools()
            >>> for info in tools:
            ...     print(f"{info.tool.name} (from {info.server})")
            ...     print(f"  Description: {info.tool.description}")
            >>>
            >>> # Call a specific tool (auto-discovers server if name not provided)
            >>> result = await client.call_tool("fetch_data", {"url": "..."})
        """
        tools_by_server = await self._list_tools_by_server(server_name)
        tool_infos: list[ToolInfo] = []
        for srv, server_tools in tools_by_server.items():
            for tool in server_tools:
                tool_infos.append(ToolInfo(tool=tool, server=srv))
        return tool_infos

    async def _list_tools_by_server(self, server_name: str | None = None) -> dict[str, list[Tool]]:
        """
        Internal method to fetch tools organized by server.

        Handles pagination automatically.
        """
        sessions = [server_name] if server_name is not None else list(self.sessions.keys())
        tools: dict[str, list[Tool]] = {}

        for session in sessions:
            if self.sessions[session].connected:
                session_tools: list[Tool] = []
                cursor: str | None = None

                while True:
                    params = PaginatedRequestParams(cursor=cursor) if cursor else None
                    result = await self.sessions[session].list_tools(params=params)

                    session_tools.extend(result.tools)

                    if result.nextCursor is None:
                        break

                    cursor = result.nextCursor

                tools[session] = session_tools

        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any], server_name: str | None = None) -> CallToolResult:
        """
        Call a tool on an MCP server.

        Args:
            tool_name: Name of the tool to call
            arguments: Arguments to pass to the tool
            server_name: Optional server name. If None, auto-discovers which server has the tool.

        Returns:
            CallToolResult containing the tool's response with content and error status.
            The content is a list of content items (text, images, embedded resources).

        Raises:
            ToolNotFoundException: If the tool is not found on any server (when server_name is None)
                                  or if the specified server is not connected/available

        Example:
            result = await client.call_tool("fetch_data", {"url": "https://api.example.com"})
            for content in result.content:
                if content.type == "text":
                    print(content.text)
        """
        if server_name is None:
            # Auto-discover which server has this tool
            tools = await self.list_tools()
            for tool_info in tools:
                if tool_info.tool.name == tool_name:
                    return await self.sessions[tool_info.server].call_tool(tool_name, arguments)

            # Tool not found - provide helpful error with available tools
            available_tools = [tool_info.tool.name for tool_info in tools]
            searched_servers = list(self.sessions.keys())
            raise ToolNotFoundException(
                tool_name,
                searched_servers=searched_servers,
                available_tools=available_tools
            )

        if server_name in self.sessions and self.sessions[server_name].connected:
            return await self.sessions[server_name].call_tool(tool_name, arguments)

        raise ToolNotFoundException(
            tool_name,
            searched_servers=[server_name],
            available_tools=[]
        )
