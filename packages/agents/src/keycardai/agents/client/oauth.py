"""User authentication client for calling agent services.

This module provides a client that handles PKCE OAuth flow for user authentication
when calling protected agent services.
"""

import asyncio
import base64
import hashlib
import logging
import re
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ..config import AgentServiceConfig

logger = logging.getLogger(__name__)


class OAuthCallbackServer:
    """Local HTTP server to handle OAuth callbacks.
    
    Starts a temporary HTTP server on localhost to receive the authorization
    code from the OAuth provider after user authentication.
    """

    def __init__(self, port: int = 8765):
        """Initialize callback server.
        
        Args:
            port: Port to listen on (default: 8765)
        """
        self.port = port
        self.code: str | None = None
        self.error: str | None = None
        self.server: HTTPServer | None = None
        self.server_thread: Thread | None = None

    def _create_handler(self):
        """Create request handler class with access to server instance."""
        server_instance = self

        class CallbackHandler(BaseHTTPRequestHandler):
            """HTTP request handler for OAuth callbacks."""

            def log_message(self, format, *args):
                """Suppress default logging."""
                pass

            def do_GET(self):
                """Handle GET request from OAuth provider."""
                # Parse query parameters
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)

                # Extract code or error
                if "code" in params:
                    server_instance.code = params["code"][0]
                    message = "✅ Authentication successful! You can close this window."
                    self.send_response(200)
                elif "error" in params:
                    server_instance.error = params["error"][0]
                    error_desc = params.get("error_description", ["Unknown error"])[0]
                    message = f"❌ Authentication failed: {error_desc}"
                    self.send_response(400)
                else:
                    message = "❌ Invalid callback - missing code or error"
                    self.send_response(400)

                # Send response
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

    async def start(self):
        """Start the callback server in a background thread."""
        self.server = HTTPServer(("localhost", self.port), self._create_handler())
        self.server_thread = Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        logger.debug(f"OAuth callback server started on port {self.port}")

    async def wait_for_code(self, timeout: int = 300) -> str:
        """Wait for authorization code from callback.
        
        Args:
            timeout: Maximum time to wait in seconds (default: 300 = 5 minutes)
            
        Returns:
            Authorization code
            
        Raises:
            TimeoutError: If code not received within timeout
            RuntimeError: If OAuth error received
        """
        elapsed = 0
        while elapsed < timeout:
            if self.code:
                return self.code
            if self.error:
                raise RuntimeError(f"OAuth error: {self.error}")
            await asyncio.sleep(0.5)
            elapsed += 0.5

        raise TimeoutError(f"OAuth callback timeout after {timeout}s")

    def stop(self):
        """Stop the callback server."""
        if self.server:
            self.server.shutdown()
            logger.debug("OAuth callback server stopped")


class AgentClient:
    """Client for calling agent services with automatic user authentication.

    This client handles PKCE OAuth flow for user authentication when calling
    protected agent services. It automatically:
    1. Detects 401 Unauthorized responses
    2. Discovers OAuth endpoints from WWW-Authenticate header
    3. Initiates PKCE flow (opens browser for user login)
    4. Exchanges authorization code for access token
    5. Retries the original request with the new token
    6. Caches tokens for subsequent requests

    This is the primary client for users and applications calling agent services.

    Example:
        >>> from keycardai.agents import AgentServiceConfig
        >>> from keycardai.agents.client import AgentClient
        >>> 
        >>> config = AgentServiceConfig(
        ...     service_name="My Application",
        ...     client_id="my_client",
        ...     client_secret="my_secret",  # Optional for confidential clients
        ...     identity_url="http://localhost:9000",
        ...     zone_id="abc123",
        ... )
        >>> 
        >>> async with AgentClient(config) as client:
        ...     result = await client.invoke(
        ...         service_url="http://localhost:8001",
        ...         task="Hello world",
        ...     )
        ...     print(result)
    """

    def __init__(
        self,
        service_config: AgentServiceConfig,
        redirect_uri: str = "http://localhost:8765/callback",
        callback_port: int = 8765,
        scopes: list[str] | None = None,
    ):
        """Initialize agent client with OAuth support.

        Args:
            service_config: Configuration of the calling application
            redirect_uri: OAuth redirect URI (must be registered with auth server)
            callback_port: Port for local callback server
            scopes: Optional list of OAuth scopes to request
        """
        self.config = service_config
        self.redirect_uri = redirect_uri
        self.callback_port = callback_port
        self.scopes = scopes or []
        self._token_cache: dict[str, str] = {}  # service_url -> access_token
        self.http_client = httpx.AsyncClient(timeout=30.0)

    def _generate_pkce_code_challenge(self, code_verifier: str) -> str:
        """Generate PKCE code challenge from verifier.
        
        Args:
            code_verifier: Random code verifier string
            
        Returns:
            Base64-URL-encoded SHA256 hash of the verifier
        """
        digest = hashlib.sha256(code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def _extract_resource_metadata_url(self, www_authenticate: str) -> str | None:
        """Extract resource metadata URL from WWW-Authenticate header.
        
        Args:
            www_authenticate: Value of WWW-Authenticate header
            
        Returns:
            Resource metadata URL or None if not found
        """
        # Parse WWW-Authenticate header for resource_metadata parameter
        match = re.search(r'resource_metadata="([^"]+)"', www_authenticate)
        if match:
            return match.group(1)
        return None

    async def _fetch_resource_metadata(self, metadata_url: str) -> dict[str, Any]:
        """Fetch OAuth protected resource metadata.
        
        Args:
            metadata_url: URL to fetch metadata from
            
        Returns:
            Resource metadata dictionary
            
        Raises:
            httpx.HTTPStatusError: If metadata fetch fails
        """
        response = await self.http_client.get(metadata_url)
        response.raise_for_status()
        return response.json()

    async def _fetch_authorization_server_metadata(
        self, auth_server_url: str
    ) -> dict[str, Any]:
        """Fetch authorization server metadata.
        
        Args:
            auth_server_url: Base URL of authorization server
            
        Returns:
            Authorization server metadata dictionary
            
        Raises:
            httpx.HTTPStatusError: If metadata fetch fails
        """
        # Try standard OAuth discovery endpoint
        discovery_url = f"{auth_server_url.rstrip('/')}/.well-known/oauth-authorization-server"
        response = await self.http_client.get(discovery_url)
        response.raise_for_status()
        return response.json()

    async def authenticate(
        self,
        service_url: str,
        www_authenticate_header: str,
    ) -> str:
        """Discover OAuth endpoints and obtain access token via PKCE flow.

        This method:
        1. Discovers OAuth metadata from WWW-Authenticate header
        2. Starts local callback server
        3. Opens browser to authorization endpoint
        4. Waits for user to authorize
        5. Exchanges authorization code for access token

        Args:
            service_url: Base URL of the target service
            www_authenticate_header: WWW-Authenticate header value from 401 response

        Returns:
            Access token

        Raises:
            ValueError: If OAuth discovery fails or metadata is invalid
            httpx.HTTPStatusError: If token exchange fails
            TimeoutError: If user doesn't complete authorization in time
        """
        logger.info("🔐 OAuth Discovery Flow Started")
        logger.info(f"   Service URL: {service_url}")

        # Step 1: Extract resource metadata URL
        metadata_url = self._extract_resource_metadata_url(www_authenticate_header)
        if not metadata_url:
            raise ValueError("No resource_metadata URL in WWW-Authenticate header")

        logger.info(f"   Resource metadata URL: {metadata_url}")

        # Step 2: Fetch resource metadata
        resource_metadata = await self._fetch_resource_metadata(metadata_url)
        logger.info("   ✅ Resource metadata fetched")

        # Step 3: Get authorization server URL
        auth_servers = resource_metadata.get("authorization_servers", [])
        if not auth_servers:
            raise ValueError("No authorization servers in resource metadata")

        auth_server_url = auth_servers[0]
        if not auth_server_url.endswith("/"):
            auth_server_url += "/"

        logger.info(f"   Authorization server: {auth_server_url}")

        # Step 4: Fetch authorization server metadata
        auth_server_metadata = await self._fetch_authorization_server_metadata(
            auth_server_url
        )

        authorization_endpoint = auth_server_metadata.get("authorization_endpoint")
        token_endpoint = auth_server_metadata.get("token_endpoint")

        if not authorization_endpoint or not token_endpoint:
            raise ValueError("Missing authorization_endpoint or token_endpoint in metadata")

        logger.info(f"   Token endpoint: {token_endpoint}")
        logger.info(f"   Authorization endpoint: {authorization_endpoint}")

        # Step 5: Start local callback server
        callback_server = OAuthCallbackServer(self.callback_port)
        await callback_server.start()
        await asyncio.sleep(0.5)  # Give server time to start

        try:
            # Step 6: Generate PKCE parameters
            code_verifier = secrets.token_urlsafe(64)
            code_challenge = self._generate_pkce_code_challenge(code_verifier)
            state = secrets.token_urlsafe(32)

            logger.info("🌐 Starting PKCE flow...")
            logger.info(f"   Redirect URI: {self.redirect_uri}")
            logger.info(f"   Scopes: {', '.join(self.scopes) if self.scopes else '(none - resource-based authorization)'}")

            # Step 7: Build authorization URL
            auth_params = {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "resource": service_url,  # Request token for base service URL
            }

            if self.scopes:
                auth_params["scope"] = " ".join(self.scopes)

            authorization_url = f"{authorization_endpoint}?{urlencode(auth_params)}"

            # Log authorization parameters (for debugging)
            logger.info("🔗 Authorization URL Parameters:")
            for key, value in auth_params.items():
                if key in ["code_challenge", "code_verifier", "state"]:
                    logger.info(f"   {key}: {value[:20]}...")
                else:
                    logger.info(f"   {key}: {value}")

            # Step 8: Open browser for user authentication
            logger.info("🌐 Opening browser for authentication...")
            logger.info(f"   Full URL: {authorization_url}")
            webbrowser.open(authorization_url)

            # Step 9: Wait for authorization code
            code = await callback_server.wait_for_code()
            logger.info("✅ Authorization code received!")
            logger.info(f"   Code (first 20 chars): {code[:20]}...")

            # Step 10: Exchange code for token
            logger.info("🔄 Exchanging authorization code for access token...")

            token_params = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.config.client_id,
                "code_verifier": code_verifier,
                "resource": service_url,  # Request token for base service URL
            }

            # Add client authentication for confidential clients
            auth_tuple = None
            if self.config.client_secret:
                auth_tuple = (self.config.client_id, self.config.client_secret)
                logger.info("   Client auth: Basic (confidential client)")
            else:
                logger.info("   Client auth: None (public client)")

            # Log token exchange parameters (for debugging)
            logger.info(f"   Token endpoint: {token_endpoint}")
            logger.info("   Parameters:")
            for key, value in token_params.items():
                if key in ["code", "code_verifier"]:
                    logger.info(f"      {key}: {value[:20]}...")
                else:
                    logger.info(f"      {key}: {value}")

            response = await self.http_client.post(
                token_endpoint,
                data=token_params,
                auth=auth_tuple,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                logger.error(f"Token request failed with status {response.status_code}")
                logger.error(f"Error response: {response.text}")

            response.raise_for_status()
            token_data = response.json()

            token = token_data["access_token"]
            logger.info("✅ Token obtained successfully!")
            logger.info(f"   Token (first 20 chars): {token[:20]}...")

            # Cache token for future requests
            self._token_cache[service_url] = token

            return token

        finally:
            # Always stop callback server
            callback_server.stop()

    async def invoke(
        self,
        service_url: str,
        task: str | dict[str, Any],
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke remote agent service with automatic OAuth handling.

        Calls the /invoke endpoint on the target service. If authentication
        fails (401), automatically initiates OAuth discovery and PKCE flow.

        Args:
            service_url: Base URL of the target service (e.g., "http://localhost:8001")
            task: Task description or parameters
            inputs: Optional additional inputs

        Returns:
            Service response (result and delegation_chain)

        Raises:
            httpx.HTTPStatusError: If request fails (other than auth)
            ValueError: If OAuth discovery fails
        """
        invoke_url = f"{service_url.rstrip('/')}/invoke"

        # Prepare request payload
        payload = {
            "task": task,
            "inputs": inputs,
        }

        # Try cached token first
        token = self._token_cache.get(service_url)
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            logger.debug(f"Using cached token for {service_url}")

        # Make request
        try:
            response = await self.http_client.post(
                invoke_url,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            # If 401, try OAuth discovery
            if e.response.status_code == 401:
                logger.info(f"Retry failed with status {e.response.status_code}")
                logger.info(f"Response headers: {dict(e.response.headers)}")
                logger.info(f"Response body: {e.response.text}")

                www_authenticate = e.response.headers.get("WWW-Authenticate")
                if not www_authenticate:
                    logger.error("No WWW-Authenticate header in 401 response")
                    raise

                # Clear cached token
                self._token_cache.pop(service_url, None)

                # Get new token via OAuth discovery
                try:
                    new_token = await self.authenticate(
                        service_url, www_authenticate
                    )
                except Exception as oauth_error:
                    logger.error(f"OAuth discovery/authentication failed: {oauth_error}")
                    raise

                # Retry with new token
                headers["Authorization"] = f"Bearer {new_token}"
                response = await self.http_client.post(
                    invoke_url,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                return response.json()

            # Re-raise other errors
            raise

    async def discover_service(self, service_url: str) -> dict[str, Any]:
        """Fetch agent card from remote service.

        Args:
            service_url: Base URL of the service

        Returns:
            Agent card dictionary

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        card_url = f"{service_url.rstrip('/')}/.well-known/agent-card.json"
        response = await self.http_client.get(card_url)
        response.raise_for_status()
        return response.json()

    async def close(self):
        """Close HTTP client and cleanup resources."""
        await self.http_client.aclose()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()


# Backward compatibility alias
A2AServiceClientWithOAuth = AgentClient
