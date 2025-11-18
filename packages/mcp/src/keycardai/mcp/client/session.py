import asyncio
from enum import Enum
from typing import TYPE_CHECKING, Any

from mcp import ClientSession

from .auth.events import CompletionEvent
from .connection import Connection, create_connection
from .context import Context
from .logging_config import get_logger

if TYPE_CHECKING:
    from .auth.coordinators.base import AuthCoordinator

logger = get_logger(__name__)

class SessionStatus(Enum):
    """
    Comprehensive status tracking for Session lifecycle.

    States are organized into categories:
    - Initial: INITIALIZING
    - Active Connection: CONNECTING, AUTHENTICATING, AUTH_PENDING, CONNECTED
    - Disconnection: DISCONNECTING, DISCONNECTED
    - Failure States: AUTH_FAILED, CONNECTION_FAILED, SERVER_UNREACHABLE, FAILED
    - Recovery: RECONNECTING

    See SESSION_STATUS_DESIGN.md for detailed state transitions and sequence diagrams.
    """

    # Initial state
    INITIALIZING = "initializing"
    """Session object created but not yet connected."""

    # Connection states
    CONNECTING = "connecting"
    """Attempting to establish connection to server."""

    AUTHENTICATING = "authenticating"
    """Connection established, authentication in progress."""

    AUTH_PENDING = "auth_pending"
    """Waiting for external auth completion (e.g., OAuth callback)."""

    CONNECTED = "connected"
    """Fully connected and operational."""

    # Disconnection states
    DISCONNECTING = "disconnecting"
    """Gracefully shutting down connection."""

    DISCONNECTED = "disconnected"
    """Clean disconnect, can reconnect."""

    # Failure states
    AUTH_FAILED = "auth_failed"
    """Authentication failed (invalid credentials, etc.)."""

    CONNECTION_FAILED = "connection_failed"
    """Failed to establish connection."""

    SERVER_UNREACHABLE = "server_unreachable"
    """Server not responding/not available."""

    FAILED = "failed"
    """General failure state."""

    # Recovery state
    RECONNECTING = "reconnecting"
    """Attempting to reconnect after failure."""


class SessionStatusCategory:
    """Helper for checking status categories."""

    ACTIVE_STATES = {
        SessionStatus.CONNECTING,
        SessionStatus.AUTHENTICATING,
        SessionStatus.CONNECTED,
        SessionStatus.RECONNECTING
    }

    DISCONNECTED_STATES = {
        SessionStatus.DISCONNECTED,
        SessionStatus.INITIALIZING
    }

    FAILURE_STATES = {
        SessionStatus.AUTH_FAILED,
        SessionStatus.CONNECTION_FAILED,
        SessionStatus.SERVER_UNREACHABLE,
        SessionStatus.FAILED
    }

    PENDING_STATES = {
        SessionStatus.AUTH_PENDING
    }

    TERMINAL_STATES = {
        SessionStatus.DISCONNECTED,
        SessionStatus.FAILED
    }

    RECOVERABLE_STATES = {
        SessionStatus.CONNECTION_FAILED,
        SessionStatus.SERVER_UNREACHABLE,
        SessionStatus.AUTH_FAILED
    }

class Session:
    """
    Session represents a connection to a single MCP server.

    Each session has its own connection with isolated auth and storage.

    **Connection Status Lifecycle:**

    Sessions track their connection state through a comprehensive status lifecycle.
    The `connect()` method does NOT raise exceptions for connection failures. Instead,
    it sets the appropriate session status. Callers should check `session.status` or
    `session.is_operational` after calling `connect()` to determine the outcome.

    Status states include:
    - INITIALIZING, CONNECTING, AUTHENTICATING, AUTH_PENDING, CONNECTED
    - DISCONNECTING, DISCONNECTED
    - AUTH_FAILED, CONNECTION_FAILED, SERVER_UNREACHABLE, FAILED

    Use properties like `is_operational`, `is_failed`, `requires_user_action`, and
    `can_retry` to check the session state.

    **Auto-Reconnection on Auth Completion:**

    When a session enters AUTH_PENDING state (e.g., waiting for OAuth), it subscribes
    to the coordinator's completion events. When authentication completes, the session
    automatically reconnects without requiring manual intervention. This enables the
    non-blocking auth pattern where users can poll `session.requires_user_action`.

    **Forwarding to ClientSession:**

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
        self.status = SessionStatus.INITIALIZING
        self._subscribed_to_coordinator = False

        self.server_storage = context.storage.get_namespace(f"server:{server_name}")

    @property
    def connected(self) -> bool:
        """Check if session is connected (backward compatibility)."""
        return self._connected

    @property
    def is_operational(self) -> bool:
        """Can perform operations (call tools, list resources, etc.)"""
        return self.status == SessionStatus.CONNECTED

    @property
    def is_connecting(self) -> bool:
        """Currently attempting to connect."""
        return self.status in {
            SessionStatus.CONNECTING,
            SessionStatus.AUTHENTICATING,
            SessionStatus.RECONNECTING
        }

    @property
    def requires_user_action(self) -> bool:
        """Requires user action to proceed (e.g., OAuth)."""
        return self.status == SessionStatus.AUTH_PENDING

    @property
    def can_retry(self) -> bool:
        """Can attempt reconnection."""
        return self.status in SessionStatusCategory.RECOVERABLE_STATES

    @property
    def is_failed(self) -> bool:
        """In a failure state."""
        return self.status in SessionStatusCategory.FAILURE_STATES

    async def on_completion_handled(self, event: CompletionEvent) -> None:
        """
        Handle auth completion notification from coordinator (CompletionSubscriber protocol).

        When OAuth completes for this session, automatically reconnect.
        This enables the non-blocking auth pattern where callers can poll
        `session.requires_user_action` and the session will automatically
        become operational when auth completes.

        Args:
            event: Completion event from coordinator
        """
        if not event.success:
            logger.debug(f"Session {self.server_name}: Ignoring failed completion event")
            return

        # Check if this completion is for this session
        event_context_id = event.result.get("context_id")
        event_server_name = event.result.get("server_name")

        if event_context_id != self.context.id or event_server_name != self.server_name:
            # Not for this session
            return

        # Auth completed for this session!
        if self.status == SessionStatus.AUTH_PENDING:
            logger.info(
                f"Session {self.server_name}: Auth completion detected, "
                f"auto-reconnecting..."
            )
            try:
                await self.connect(_retry_after_auth=False)
            except Exception as e:
                logger.error(
                    f"Session {self.server_name}: Auto-reconnect after auth failed: {e}",
                    exc_info=True
                )

    def _set_status(self, new_status: SessionStatus, reason: str = "") -> None:
        """
        Set session status with logging.

        Args:
            new_status: New status to set
            reason: Optional reason for status change
        """
        old_status = self.status
        self.status = new_status

        log_msg = f"Session {self.server_name}: {old_status.value} -> {new_status.value}"
        if reason:
            log_msg += f" ({reason})"

        logger.info(log_msg)

    def _classify_connection_error(self, error: Exception) -> SessionStatus:
        """
        Classify an error into appropriate terminal status.

        Args:
            error: Exception that occurred

        Returns:
            Appropriate terminal SessionStatus
        """
        error_str = str(error).lower()

        # Network/connectivity issues
        if any(phrase in error_str for phrase in [
            'connection refused',
            'connection reset',
            'connection timed out',
            'name or service not known',  # DNS
            'nodename nor servname provided',  # DNS
            'no route to host',
            'network unreachable',
            'host unreachable',
            'getaddrinfo failed',  # DNS resolution
        ]):
            return SessionStatus.SERVER_UNREACHABLE

        # Authentication issues
        if any(phrase in error_str for phrase in [
            'unauthorized',
            'authentication failed',
            'invalid credentials',
            'forbidden',
            'auth',
        ]):
            return SessionStatus.AUTH_FAILED

        # Connection was established but lost
        if any(phrase in error_str for phrase in [
            'connection closed',
            'connection lost',
            'broken pipe',
            'eof',
        ]):
            return SessionStatus.CONNECTION_FAILED

        return SessionStatus.FAILED

    async def connect(self, _retry_after_auth: bool = True) -> None:
        """
        Connect to the server.

        This method does not raise exceptions for connection failures. Instead, it sets
        the appropriate session status. Callers should check session.status or
        session.is_operational after calling this method.

        Possible outcomes:
        - status=CONNECTED: Successfully connected and ready
        - status=AUTH_PENDING: Requires user authentication (check get_auth_challenge)
        - status=SERVER_UNREACHABLE: Server not reachable
        - status=CONNECTION_FAILED: Connection failed
        - status=AUTH_FAILED: Authentication failed
        - status=FAILED: Other failure

        Args:
            _retry_after_auth: Internal flag to retry once after auth challenge completes
        """
        if self._connected and self._session:
            # Already connected - verify health before returning
            is_healthy = await self.check_connection_health()
            if is_healthy:
                logger.debug(f"Session {self.server_name} already connected and healthy")
                return
            # Health check failed - connection is dead, continue to reconnect
            logger.info(f"Session {self.server_name} connection unhealthy, reconnecting")

        if self._connected:
            await self.disconnect()

        if self._connection is None:
            try:
                self._connection = create_connection(
                    server_name=self.server_name,
                    server_config=self.server_config,
                    context=self.context,
                    coordinator=self.coordinator,
                    server_storage=self.server_storage
                )
            except Exception as e:
                await self._handle_connection_failure(e)
                return

        self._set_status(SessionStatus.CONNECTING, "establishing transport connection")
        try:
            read_stream, write_stream = await self._connection.start()
        except Exception as e:
            await self._handle_connection_failure(e)
            return

        try:
            self._session = ClientSession(read_stream, write_stream)
            await self._session.__aenter__()
            self._connected = True
        except Exception as e:
            logger.error(f"Failed to create MCP session: {e}", exc_info=True)
            await self._cleanup_failed_connection()
            self._set_status(SessionStatus.FAILED, f"session creation failed: {str(e)[:100]}")
            return

        await self._initialize_session(_retry_after_auth)

    async def _handle_connection_failure(self, error: Exception) -> None:
        """
        Handle failures during connection establishment.

        Args:
            error: The exception that occurred during connection
        """
        logger.error("Failed to establish connection")
        await self._cleanup_failed_connection()

        error_status = self._classify_connection_error(error)
        self._set_status(error_status, "connection failed")

    async def _cleanup_failed_connection(self) -> None:
        """Clean up resources after a failed connection attempt."""
        if self._connection:
            try:
                await self._connection.stop()
            except Exception:
                logger.debug("Error stopping connection during cleanup (suppressed)")

        self._connected = False
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                logger.debug("Error closing session during cleanup (suppressed)")
            finally:
                self._session = None

    async def _initialize_session(self, retry_after_auth: bool) -> None:
        """
        Initialize the MCP session and handle authentication.

        Sets appropriate session status based on initialization outcome.
        Does not raise exceptions - communicates via status instead.

        Args:
            retry_after_auth: Whether to retry connection once after auth completion
        """
        self._set_status(SessionStatus.AUTHENTICATING, "initializing session")

        try:
            await self._session.initialize()
            self._set_status(SessionStatus.CONNECTED, "session initialized")

        except Exception as e:
            auth_challenge = await self.get_auth_challenge()
            if auth_challenge:
                await self.disconnect()

                # Subscribe to coordinator for auto-reconnect when auth completes
                if not self._subscribed_to_coordinator:
                    self.coordinator.subscribe(self)
                    self._subscribed_to_coordinator = True
                    logger.debug(
                        f"Session {self.server_name}: Subscribed to coordinator "
                        f"for auth completion notifications"
                    )

                self._set_status(SessionStatus.AUTH_PENDING, "authentication required")
                logger.debug(f"Session {self.server_name} requires authentication")
                return

            if retry_after_auth and "Connection closed" in str(e):
                logger.debug("Connection closed after auth, retrying connection...")
                await self.disconnect()
                self._connection = None
                await self.connect(_retry_after_auth=False)
                return

            logger.error("Session initialization failed")
            await self.disconnect()
            error_status = self._classify_connection_error(e)
            self._set_status(error_status, "initialization failed")

    async def disconnect(self) -> None:
        """Gracefully disconnect from the server."""
        if not self._connected:
            return

        self._set_status(SessionStatus.DISCONNECTING, "initiating disconnect")
        self._connected = False

        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                logger.debug("Error closing session (suppressed)")
            finally:
                self._session = None

        if self._connection:
            try:
                await self._connection.stop()
            except Exception:
                logger.debug("Error stopping connection (suppressed)")
            finally:
                self._connection = None

        # Unsubscribe from coordinator
        if self._subscribed_to_coordinator:
            self.coordinator.unsubscribe(self)
            self._subscribed_to_coordinator = False
            logger.debug(f"Session {self.server_name}: Unsubscribed from coordinator")

        self._set_status(SessionStatus.DISCONNECTED, "disconnect complete")

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

    async def check_connection_health(self) -> bool:
        """
        Proactively check if the connection is still alive.

        This method sends a ping to the server to verify the connection is operational.
        If the connection is dead, it updates the session status accordingly.

        Returns:
            True if connection is healthy, False if connection is dead

        Note:
            This is a lightweight check using the MCP ping protocol. It doesn't
            guarantee the next operation will succeed, but it can detect if the
            connection is clearly dead.
        """
        if not self._connected or not self._session:
            return False

        try:
            await self._session.send_ping()
            return True
        except Exception as e:
            logger.warning("Connection health check failed")
            self._connected = False
            error_status = self._classify_connection_error(e)
            self._set_status(error_status, "connection health check failed")
            return False
