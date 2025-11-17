import asyncio
from typing import TYPE_CHECKING, Any

from mcp import ClientSession

from .connection import Connection, create_connection
from .context import Context
from .logging_config import get_logger

if TYPE_CHECKING:
    from .auth.coordinators.base import AuthCoordinator

logger = get_logger(__name__)


class Session:
    """
    Session represents a connection to a single MCP server.

    Each session has its own connection with isolated auth and storage.

    This class automatically forwards all methods from mcp.ClientSession via __getattr__,
    allowing it to stay in sync with upstream MCP SDK changes.

    For IDE autocomplete, see session.pyi type stub which defines all available methods.
    """

    def __init__(
        self,
        server_name: str,
        server_config: dict[str, Any],
        context: Context,
        coordinator: "AuthCoordinator"
    ):
        """
        Initialize session.

        Args:
            server_name: Name of the server
            server_config: Server configuration
            context: Context for identity and storage
            coordinator: Auth coordinator for callbacks
        """
        self.server_name = server_name
        self.server_config = server_config
        self.context = context
        self.coordinator = coordinator
        self._session = None
        self._connection: Connection | None = None
        self._connected = False

        self.server_storage = context.storage.get_namespace(f"server:{server_name}")

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, _retry_after_auth: bool = True) -> None:
        """
        Connect to the server.

        Args:
            _retry_after_auth: Internal flag to retry once after auth challenge completes
        """
        if self._connected and self._session:
            # TODO: Add a ping/health check here
            logger.debug(f"Session {self.server_name} already connected")
            return

        if self._connected:
            await self.disconnect()

        if self._connection is None:
            self._connection = create_connection(
                server_name=self.server_name,
                server_config=self.server_config,
                context=self.context, # TODO: this has access to client storage
                coordinator=self.coordinator,
                server_storage=self.server_storage  # Pass our server-scoped storage
            )

        try:
            read_stream, write_stream = await self._connection.start()

            self._session = ClientSession(
                read_stream,
                write_stream
            )
            await self._session.__aenter__()
            self._connected = True

            # Try to initialize the session
            # If auth is required, this will trigger the auth strategy (which may block)
            await self._session.initialize()

        except Exception as e:
            auth_challenge = await self.get_auth_challenge()

            if auth_challenge:
                await self.disconnect()
                logger.debug(f"Session {self.server_name} has pending auth challenge")
                # Don't raise - let caller check for pending auth
                return  # Early return - don't retry when auth is pending

            # Try to reconnect once automatically
            # TODO: refactor this string check in error
            if _retry_after_auth and "Connection closed" in str(e):
                logger.debug("Connection closed (likely after auth completion), retrying...")
                await self.disconnect()
                self._connection = None
                await self.connect(_retry_after_auth=False)
            else:
                logger.error(f"Error initializing session: {e}", exc_info=True)
                await self.disconnect()
                raise

    async def disconnect(self) -> None:
        if not self._connected:
            return

        self._connected = False

        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"Error closing session (suppressed): {e}")
            finally:
                self._session = None

        if self._connection:
            try:
                await self._connection.stop()
            except Exception as e:
                logger.debug(f"Error stopping connection (suppressed): {e}")
            finally:
                self._connection = None

    async def requires_auth(self) -> bool:
        """Check if this session has a pending auth challenge."""
        return await self.get_auth_challenge() is not None

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
        # Auth challenge is stored in coordinator
        return await self.coordinator.get_auth_pending(
            context_id=self.context.id,
            server_name=self.server_name
        )

    def __getattr__(self, name: str) -> Any:
        """
        Delegate unknown attributes to underlying MCP ClientSession.

        This allows Session to automatically forward all ClientSession methods
        without explicit wrapper code, making it easy to stay in sync with
        upstream MCP SDK changes.

        Methods that require custom logic (like list_tools with pagination)
        can be explicitly defined in this class and will take precedence.

        Args:
            name: Attribute name to access

        Returns:
            The attribute from the underlying ClientSession, wrapped if it's a method

        Raises:
            RuntimeError: If session is not connected
            AttributeError: If attribute doesn't exist on ClientSession
        """
        if self._session is None:
            raise RuntimeError(
                f"Cannot access '{name}': session not connected. "
                f"Call connect() first."
            )

        try:
            attr = getattr(self._session, name)
        except AttributeError:
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            ) from None

        # If it's a coroutine function, wrap it to check connection state
        if callable(attr) and asyncio.iscoroutinefunction(attr):
            async def wrapped(*args, **kwargs):
                if not self._connected:
                    raise RuntimeError(
                        f"Cannot call '{name}': session not connected"
                    )
                return await attr(*args, **kwargs)
            return wrapped

        return attr
