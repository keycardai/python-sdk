"""Local HTTP server for receiving OAuth authorization code callbacks."""

import asyncio
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


class OAuthCallbackServer:
    """Local HTTP server that captures the authorization code from a redirect.

    Starts an :class:`http.server.HTTPServer` on ``localhost`` to receive the
    authorization code from the OAuth provider after the user authorizes in
    their browser. Exposes the captured ``code`` (or ``error``) via
    :meth:`wait_for_code`.

    Intended for desktop / CLI public clients running the PKCE flow against a
    loopback redirect URI (RFC 8252).
    """

    def __init__(self, port: int = 8765):
        self.port = port
        self.code: str | None = None
        self.error: str | None = None
        self.server: HTTPServer | None = None
        self.server_thread: Thread | None = None

    def _create_handler(self):
        server_instance = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)

                if "code" in params:
                    server_instance.code = params["code"][0]
                    message = "Authentication successful. You can close this window."
                    self.send_response(200)
                elif "error" in params:
                    server_instance.error = params["error"][0]
                    error_desc = params.get("error_description", ["Unknown error"])[0]
                    message = f"Authentication failed: {error_desc}"
                    self.send_response(400)
                else:
                    message = "Invalid callback: missing code or error."
                    self.send_response(400)

                self.send_header("Content-type", "text/html")
                self.end_headers()
                html = f"""
                <html>
                <head><title>OAuth Callback</title></head>
                <body>
                    <h1>{message}</h1>
                    <p>This window will close automatically...</p>
                    <script>setTimeout(() => window.close(), 2000);</script>
                </body>
                </html>
                """
                self.wfile.write(html.encode())

        return CallbackHandler

    async def start(self) -> None:
        """Start the callback server in a background thread."""
        self.server = HTTPServer(("localhost", self.port), self._create_handler())
        self.server_thread = Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        logger.debug("OAuth callback server started on port %d", self.port)

    async def wait_for_code(self, timeout: int = 300) -> str:
        """Wait for the authorization code from the redirect.

        Args:
            timeout: Maximum time to wait, in seconds.

        Returns:
            The authorization code.

        Raises:
            TimeoutError: If no code is received within the timeout.
            RuntimeError: If the redirect carried an OAuth error parameter.
        """
        elapsed = 0.0
        while elapsed < timeout:
            if self.code:
                return self.code
            if self.error:
                raise RuntimeError(f"OAuth error: {self.error}")
            await asyncio.sleep(0.5)
            elapsed += 0.5
        raise TimeoutError(f"OAuth callback timeout after {timeout}s")

    def stop(self) -> None:
        """Shut the callback server down."""
        if self.server:
            self.server.shutdown()
            logger.debug("OAuth callback server stopped")
