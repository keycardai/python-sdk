"""Redirect endpoint management for OAuth flows."""

import asyncio
import socket
import webbrowser
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiohttp import web

from ...logging_config import get_logger

logger = get_logger(__name__)


class EndpointManager(ABC):
    """
    Abstract base for managing OAuth redirect endpoints.
    """

    @abstractmethod
    async def get_redirect_uris(self) -> list[str]:
        """
        Get available redirect URIs for OAuth registration.

        May start infrastructure (e.g., local server) if needed.

        Returns:
            List of redirect URIs
        """
        pass

    @abstractmethod
    async def initiate_redirect(self, url: str, metadata: dict[str, Any]) -> None:
        """
        Initiate user redirect to authorization URL.

        Args:
            url: Authorization URL to redirect user to
            metadata: Flow metadata (e.g., state, server_name)
        """
        pass


class LocalEndpointManager(EndpointManager):
    """
    Local HTTP server endpoint manager for CLI/desktop applications.

    Runs a local HTTP server to receive OAuth callbacks. The server is
    shared across all auth flows and routes callbacks to a handler.

    Key behaviors:
    - Starts local HTTP server on demand (lazy initialization)
    - Opens user's browser to authorization URL (configurable)
    - BLOCKS in initiate_redirect() until callback is received (configurable)
    - Tracks pending flows by OAuth state parameter

    Use for: CLI apps, desktop apps, local development.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 0,
        callback_path: str = "/callback",
        auto_open_browser: bool = True,
        block_until_callback: bool = True
    ):
        """
        Initialize local endpoint manager.

        Args:
            host: Host for local server (default: localhost)
            port: Port for local server (0 = auto-assign)
            callback_path: Path for callback endpoint (default: /callback)
            auto_open_browser: Whether to automatically open browser (default: True)
            block_until_callback: Whether to block until callback received (default: True)
        """
        self._host = host
        self._desired_port = port
        self._actual_port: int | None = None
        self._callback_path = callback_path
        self._auto_open_browser = auto_open_browser
        self._block_until_callback = block_until_callback

        self._server_task: asyncio.Task | None = None
        self._ready_event = asyncio.Event()
        self._shutdown_event = asyncio.Event()

        self._pending_flows: dict[str, asyncio.Event] = {}

        self._completion_handler: Any = None

    def set_callback_handler(self, handler: Any) -> None:
        """
        Set the completion handler to invoke when OAuth callbacks are received.

        The handler should be an async function that takes params dict
        and returns a result dict.

        Args:
            handler: Async completion handler function (e.g., coordinator.handle_completion)
        """
        self._completion_handler = handler

    async def get_redirect_uris(self) -> list[str]:
        """
        Get redirect URI for local server.

        Starts the local server if not already running (lazy initialization).

        Returns:
            List with single redirect URI (e.g., ["http://localhost:8080/callback"])
        """
        if self._actual_port is None:
            port = self._desired_port or self._find_free_port()
            self._actual_port = port

            logger.debug(f"Local callback server will use: http://{self._host}:{port}{self._callback_path}")

            self._server_task = asyncio.create_task(
                self._run_server(port, self._shutdown_event),
                name="LocalEndpointManager_server"
            )

            await self._ready_event.wait()
            logger.info(f"Local callback server running on http://{self._host}:{port}")

        return [f"http://{self._host}:{self._actual_port}{self._callback_path}"]

    async def initiate_redirect(self, url: str, metadata: dict[str, Any]) -> None:
        """
        Initiate OAuth redirect (configurable browser opening and blocking).

        Behavior depends on configuration:
        - auto_open_browser=True: Opens browser automatically
        - auto_open_browser=False: Logs URL for manual opening
        - block_until_callback=True: Blocks until callback received
        - block_until_callback=False: Returns immediately

        Args:
            url: Authorization URL to open in browser
            metadata: Flow metadata including server_name

        Raises:
            TimeoutError: If authorization not completed within 300 seconds (when blocking)
        """
        server_name = metadata.get("server_name", "unknown")

        state = self._extract_state_from_url(url)
        if not state:
            logger.error("No state found in authorization URL")
            raise ValueError("Authorization URL missing state parameter")

        if state not in self._pending_flows:
            self._pending_flows[state] = asyncio.Event()

        if self._auto_open_browser:
            logger.info(f"Opening browser for auth flow (server: {server_name})")
            logger.info("Please authorize in your browser...")
            webbrowser.open(url)
        else:
            logger.info(f"Authorization required for {server_name}")
            logger.info(f"Please visit: {url}")

        if self._block_until_callback:
            logger.info("Waiting for authorization to complete...")
            try:
                await asyncio.wait_for(self._pending_flows[state].wait(), timeout=300)
                logger.info(f"Authorization completed for {server_name}")
            except asyncio.TimeoutError as e:
                logger.error("Authorization timed out after 300s")
                self._pending_flows.pop(state, None)
                raise TimeoutError(f"Authorization timed out for {server_name}") from e
        else:
            logger.debug(f"Non-blocking mode: returning immediately for {server_name}")

    async def shutdown(self) -> None:
        """
        Stop local callback server.

        Gracefully shuts down the HTTP server and cleans up resources.
        """
        if self._server_task:
            self._shutdown_event.set()
            try:
                await asyncio.wait_for(self._server_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Local server shutdown timed out")
                self._server_task.cancel()
            self._server_task = None
            self._actual_port = None
            self._ready_event.clear()
            self._shutdown_event.clear()

    def _find_free_port(self) -> int:
        """Find an available port on localhost."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((self._host, 0))
            s.listen(1)
            return s.getsockname()[1]

    def _extract_state_from_url(self, url: str) -> str | None:
        """Extract OAuth state parameter from authorization URL."""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        state_list = params.get('state', [])
        return state_list[0] if state_list else None

    async def _run_server(self, port: int, shutdown_event: asyncio.Event) -> None:
        """
        Run aiohttp server for OAuth callbacks.

        The server is shared across all contexts using this endpoint manager.
        """
        app = web.Application()
        app.router.add_get(self._callback_path, self._handle_completion_request)

        runner = web.AppRunner(app)
        await runner.setup()

        site = web.TCPSite(runner, self._host, port)
        await site.start()

        self._ready_event.set()

        try:
            await shutdown_event.wait()
        finally:
            await runner.cleanup()

    async def _handle_completion_request(self, request: web.Request) -> web.Response:
        """
        Handle HTTP callback request from OAuth provider and process completion.

        Routes to the registered completion handler and signals flow completion.
        """
        try:
            params = dict(request.query)
            state = params.get('state')

            if not state:
                return web.Response(
                    text="Error: Missing state parameter",
                    status=400
                )

            if not self._completion_handler:
                return web.Response(
                    text="Error: No completion handler registered",
                    status=500
                )

            result = await self._completion_handler(params)
            server_name = result.get('server_name', 'unknown')

            if state in self._pending_flows:
                self._pending_flows[state].set()
                self._pending_flows.pop(state, None)

            return web.Response(
                text=f"✅ Authorization successful for server '{server_name}'! You can close this window.",
                status=200
            )
        except Exception as e:
            logger.error(f"Error handling completion: {e}", exc_info=True)

            state = request.query.get('state')
            if state and state in self._pending_flows:
                self._pending_flows.pop(state, None)

            return web.Response(
                text="❌ Authentication callback failed. Please try again.",
                status=400
            )


class RemoteEndpointManager(EndpointManager):
    """
    Remote endpoint manager for web applications.

    Uses an external redirect URI (provided by the web application).
    Does not run its own server or open browsers - the web app handles
    all HTTP interactions.

    Key behaviors:
    - Returns configured redirect URI
    - initiate_redirect() is a no-op (web app handles redirect via HTTP)
    - No blocking behavior (suitable for async web frameworks)

    Use for: Web apps, APIs, microservices (FastAPI, Flask, Starlette, etc.)
    """

    def __init__(self, redirect_uri: str):
        """
        Initialize remote endpoint manager.

        Args:
            redirect_uri: OAuth callback URL (e.g., "https://myapp.com/oauth/callback")
        """
        self.redirect_uri = redirect_uri

    async def get_redirect_uris(self) -> list[str]:
        """
        Get configured redirect URI.

        Returns:
            List with single redirect URI
        """
        return [self.redirect_uri]

    async def initiate_redirect(self, url: str, metadata: dict[str, Any]) -> None:
        """
        No-op for remote redirects.

        Web applications handle redirects via HTTP responses, not by
        opening browsers programmatically. The authorization URL is
        stored in pending auth metadata and returned to the client.

        Args:
            url: Authorization URL (not used)
            metadata: Flow metadata for logging
        """
        server_name = metadata.get("server_name", "unknown")
        logger.debug(f"Authorization URL ready for: {server_name}")


