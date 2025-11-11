"""
Type stub for Session class to provide IDE autocomplete for delegated methods.

This stub defines all methods available on Session, including:
1. Methods explicitly defined in Session (connect, disconnect, list_tools, etc.)
2. Methods delegated to mcp.ClientSession via __getattr__

When upstream mcp.ClientSession adds new methods, update this stub to include them.
"""

from datetime import timedelta
from typing import Any, Protocol

from mcp.shared.message import ClientMessageMetadata, ServerMessageMetadata
from mcp.shared.session import ReceiveResultT, SendNotificationT, SendRequestT
from mcp.types import (
    AnyUrl,
    CallToolResult,
    CompleteResult,
    EmptyResult,
    GetPromptResult,
    InitializeResult,
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
    LoggingLevel,
    PaginatedRequestParams,
    PromptReference,
    ReadResourceResult,
    ResourceTemplateReference,
)

from .context import Context

# Type for progress callback (matching upstream)
class ProgressFnT(Protocol):
    async def __call__(
        self,
        progress_token: str | int,
        progress: float,
        total: float | None = None,
    ) -> None: ...

class Session:
    """
    Session represents a connection to a single MCP server.

    Each session has its own connection with isolated auth and storage.
    """

    server_name: str
    server_config: dict[str, Any]
    context: Context
    coordinator: Any  # AuthCoordinator (avoiding circular import)
    server_storage: Any  # NamespacedStorage

    def __init__(
        self,
        server_name: str,
        server_config: dict[str, Any],
        context: Context,
        coordinator: Any,  # AuthCoordinator
    ) -> None:
        """
        Initialize session.

        Args:
            server_name: Name of the server
            server_config: Server configuration
            context: Context for identity and storage
            coordinator: Auth coordinator for callbacks
        """
        ...

    @property
    def connected(self) -> bool:
        """Whether the session is currently connected."""
        ...

    # ===== Custom Session Methods =====

    async def connect(self, _retry_after_auth: bool = True) -> None:
        """
        Connect to the server.

        Args:
            _retry_after_auth: Internal flag to retry once after auth challenge completes
        """
        ...

    async def disconnect(self) -> None:
        """Disconnect from the server and clean up resources."""
        ...

    async def requires_auth(self) -> bool:
        """Check if this session has a pending auth challenge."""
        ...

    async def get_auth_challenge(self) -> dict[str, str] | None:
        """
        Get pending auth challenge for this session.

        An auth challenge is created by the auth strategy when authentication
        is required but not yet complete (e.g., waiting for OAuth callback).

        Returns:
            Dict with challenge details (strategy-specific) or None if no pending challenge.
            For OAuth: {'authorization_url': str, 'state': str}
            For other strategies: may contain different fields
        """
        ...

    # ===== Delegated MCP ClientSession Methods =====
    # These methods are forwarded to the underlying mcp.ClientSession via __getattr__

    async def initialize(self) -> InitializeResult:
        """
        Initialize the MCP session with the server.

        Sends the initialize request and waits for the server's response.
        """
        ...

    async def send_ping(self) -> EmptyResult:
        """Send a ping request to the server."""
        ...

    async def send_progress_notification(
        self,
        progress_token: str | int,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        """
        Send a progress notification to the server.

        Args:
            progress_token: Token identifying the operation
            progress: Current progress value
            total: Optional total progress value
            message: Optional progress message
        """
        ...

    async def set_logging_level(self, level: LoggingLevel) -> EmptyResult:
        """
        Set the logging level on the server.

        Args:
            level: Logging level to set
        """
        ...

    async def list_resources(
        self, *, params: PaginatedRequestParams | None = None
    ) -> ListResourcesResult:
        """
        List resources available on the server.

        Args:
            params: Optional pagination parameters

        Returns:
            List of resources (may be paginated)
        """
        ...

    async def list_resource_templates(
        self, *, params: PaginatedRequestParams | None = None
    ) -> ListResourceTemplatesResult:
        """
        List resource templates available on the server.

        Args:
            params: Optional pagination parameters

        Returns:
            List of resource templates (may be paginated)
        """
        ...

    async def read_resource(self, uri: AnyUrl) -> ReadResourceResult:
        """
        Read a resource from the server.

        Args:
            uri: URI of the resource to read

        Returns:
            Resource contents
        """
        ...

    async def subscribe_resource(self, uri: AnyUrl) -> EmptyResult:
        """
        Subscribe to resource updates.

        Args:
            uri: URI of the resource to subscribe to
        """
        ...

    async def unsubscribe_resource(self, uri: AnyUrl) -> EmptyResult:
        """
        Unsubscribe from resource updates.

        Args:
            uri: URI of the resource to unsubscribe from
        """
        ...

    async def list_prompts(
        self, *, params: PaginatedRequestParams | None = None
    ) -> ListPromptsResult:
        """
        List prompts available on the server.

        Args:
            params: Optional pagination parameters

        Returns:
            List of prompts (may be paginated)
        """
        ...

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> GetPromptResult:
        """
        Get a prompt from the server.

        Args:
            name: Name of the prompt
            arguments: Optional arguments for the prompt

        Returns:
            Prompt details and content
        """
        ...

    async def complete(
        self,
        ref: ResourceTemplateReference | PromptReference,
        argument: dict[str, str],
        context_arguments: dict[str, str] | None = None,
    ) -> CompleteResult:
        """
        Request completion suggestions.

        Args:
            ref: Reference to the resource template or prompt
            argument: Argument to complete
            context_arguments: Optional context arguments

        Returns:
            Completion suggestions
        """
        ...

    async def send_roots_list_changed(self) -> None:
        """Send a notification that the roots list has changed."""
        ...


    # ===== Additional Delegated Methods =====
    # Auto-generated by check_session_stub.py

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None, read_timeout_seconds: timedelta | None = None, progress_callback: ProgressFnT | None = None, *, meta: dict[str, Any] | None = None) -> CallToolResult:
        """Send a tools/call request with optional progress callback support."""
        ...

    async def send_notification(self, notification: ~SendNotificationT, related_request_id: str | int | None = None) -> None:
        """Emits a notification, which is a one-way message that does not expect"""
        ...

    async def send_request(self, request: ~SendRequestT, result_type: type[~ReceiveResultT], request_read_timeout_seconds: timedelta | None = None, metadata: ClientMessageMetadata | ServerMessageMetadata | None = None, progress_callback: ProgressFnT | None = None) -> ~ReceiveResultT:
        """Sends a request and wait for a response. Raises an McpError if the"""
        ...

    # ===== Additional Delegated Methods =====
    # Auto-generated by check_session_stub.py

    async def list_tools(self, cursor: str | None = None, *, params: PaginatedRequestParams | None = None) -> ListToolsResult:
        """Send a tools/list request."""
        ...

    def __getattr__(self, name: str) -> Any:
        """
        Delegate unknown attributes to underlying MCP ClientSession.

        This allows Session to automatically forward all ClientSession methods
        without explicit wrapper code.
        """
        ...

