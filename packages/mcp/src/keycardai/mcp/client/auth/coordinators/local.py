"""Local completion server coordinator for authentication flows."""


from ...logging_config import get_logger
from ...storage import StorageBackend
from .base import AuthCoordinator
from .endpoint_managers import LocalEndpointManager

logger = get_logger(__name__)


class LocalAuthCoordinator(AuthCoordinator):
    """
    Local completion server coordinator for authentication flows.

    Runs a local HTTP server to receive auth completions via LocalEndpointManager.
    Server is SHARED across all contexts using this coordinator.

    Use for: CLI apps, desktop apps, local development.

    Key behaviors:
    - Opens browser to authorization URL
    - Blocks in handle_redirect() until completion arrives
    - Requires synchronous cleanup to avoid race conditions
    """

    def __init__(
        self,
        backend: StorageBackend | None = None,
        host: str = "localhost",
        port: int = 0,
        callback_path: str = "/callback"
    ):
        """
        Initialize local coordinator with LocalEndpointManager.

        Args:
            backend: Storage backend (defaults to InMemoryBackend)
            host: Host for local server (default: localhost)
            port: Port (0 = auto-assign)
            callback_path: HTTP callback path for OAuth redirects (default: /callback)
        """
        # Create endpoint manager
        endpoint_manager = LocalEndpointManager(host, port, callback_path)

        # Initialize base coordinator with endpoint manager
        super().__init__(backend, endpoint_manager)

        # Set completion handler on endpoint manager
        # The manager will call handle_completion when requests arrive
        endpoint_manager.set_callback_handler(self.handle_completion)

    @property
    def endpoint_type(self) -> str:
        """Type of endpoint: local HTTP server."""
        return "local"

    @property
    def requires_synchronous_cleanup(self) -> bool:
        """
        LocalAuthCoordinator requires synchronous cleanup.

        This prevents race conditions with the blocking wait pattern
        in handle_redirect() which waits for completion to arrive.
        """
        return True

    async def shutdown(self) -> None:
        """
        Stop local completion server.

        Delegates to LocalEndpointManager for graceful shutdown.
        """
        if self.endpoint_manager:
            await self.endpoint_manager.shutdown()

