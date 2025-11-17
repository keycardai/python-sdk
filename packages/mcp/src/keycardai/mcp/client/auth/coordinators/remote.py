"""Remote completion coordinator for web applications."""


from starlette.responses import HTMLResponse, Response

from ...logging_config import get_logger
from ...storage import StorageBackend
from .base import AuthCoordinator
from .endpoint_managers import RemoteEndpointManager

logger = get_logger(__name__)


class StarletteAuthCoordinator(AuthCoordinator):
    """
    Remote completion coordinator for web applications.

    Uses RemoteEndpointManager which provides a redirect URI but doesn't
    run its own server. You provide the HTTP endpoint in your web framework.

    Use for: Web apps, APIs, microservices (Starlette, FastAPI, Flask, etc.)

    Key behaviors:
    - Returns configured redirect URI
    - handle_redirect() is a no-op (web app handles via HTTP)
    - Asynchronous cleanup (suitable for web frameworks)
    """

    def __init__(
        self,
        backend: StorageBackend,
        redirect_uri: str
    ):
        """
        Initialize remote coordinator with RemoteEndpointManager.

        Args:
            backend: Storage backend (required - use Redis/DynamoDB in production!)
            redirect_uri: Callback URL (e.g., "https://myapp.com/oauth/callback")
        """
        endpoint_manager = RemoteEndpointManager(redirect_uri)

        super().__init__(backend, endpoint_manager)

    @property
    def endpoint_type(self) -> str:
        """Type of endpoint: remote web application."""
        return "remote"

    def get_completion_endpoint(self):
        """
        Get HTTP endpoint handler for OAuth completions.

        This handler is protocol-agnostic and works with any auth strategy
        that uses OAuth redirect flows.

        Example with Starlette:
            coordinator = StarletteAuthCoordinator(...)
            app = Starlette(routes=[
                Route("/callback", coordinator.get_completion_endpoint())
            ])

        Returns:
            Async function that handles HTTP OAuth completion requests
        """
        async def completion_endpoint(request) -> Response:
            """HTTP endpoint for authentication completions (protocol-agnostic)."""
            try:
                if hasattr(request, 'query_params'):
                    params = dict(request.query_params)
                else:
                    params = dict(request.query)

                state = params.get('state')
                if not state:
                    return HTMLResponse(
                        content="<h1>Error</h1><p>Missing state parameter</p>",
                        status_code=400
                    )

                result = await self.handle_completion(params)
                server_name = result.get('server_name', 'unknown')

                return HTMLResponse(
                    content=f"""
                    <html>
                        <head>
                            <title>Authorization Successful</title>
                            <script>
                                // Automatically close the window after 2 seconds
                                setTimeout(function() {{
                                    window.close();
                                    // If window.close() doesn't work (e.g., not opened by script),
                                    // try alternative methods
                                    if (!window.closed) {{
                                        window.open('', '_self', '');
                                        window.close();
                                    }}
                                }}, 2000);
                            </script>
                        </head>
                        <body>
                            <h1>Authorization Successful!</h1>
                            <p>Server: {server_name}</p>
                            <p>This window will close automatically in 2 seconds...</p>
                        </body>
                    </html>
                    """,
                    status_code=200
                )
            except Exception as e:
                logger.error(f"Error handling completion: {e}", exc_info=True)
                # Don't expose internal exception details to browser
                return HTMLResponse(
                    content="<h1>Error</h1><p>Authentication callback failed. Please try again.</p>",
                    status_code=500
                )

        return completion_endpoint

