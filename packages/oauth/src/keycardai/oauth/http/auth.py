"""Authentication strategies for OAuth 2.0 clients.

This module implements HTTP Authorization header strategies for OAuth 2.0
client authentication with a clean protocol-based design.
"""

import base64
from typing import Protocol


class AuthStrategy(Protocol):
    """Protocol for OAuth 2.0 client authentication strategies.

    Defines the interface for setting the Authorization header in HTTP requests.
    All authentication strategies must implement this protocol.
    """

    def apply_headers(self) -> dict[str, str]:
        """Apply authentication headers to HTTP request.

        Returns:
            Dictionary containing Authorization header and any other auth headers
        """
        ...


class NoneAuth:
    """No authentication strategy.

    Used when no authentication is required (e.g., public endpoints,
    dynamic client registration).
    """

    def apply_headers(self) -> dict[str, str]:
        """Apply no authentication headers."""
        return {}


class BasicAuth:
    """HTTP Basic authentication strategy.

    Implements RFC 7617 HTTP Basic authentication using client credentials.
    """

    def __init__(self, client_id: str, client_secret: str):
        """Initialize Basic authentication.

        Args:
            client_id: OAuth 2.0 client identifier
            client_secret: OAuth 2.0 client secret
        """
        if not client_id:
            raise ValueError("client_id is required")
        if not client_secret:
            raise ValueError("client_secret is required")

        self.client_id = client_id
        self.client_secret = client_secret

    def apply_headers(self) -> dict[str, str]:
        """Apply HTTP Basic authentication header."""
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        return {"Authorization": f"Basic {encoded_credentials}"}


class BearerAuth:
    """HTTP Bearer token authentication strategy.

    Implements RFC 6750 Bearer token authentication using access tokens.
    """

    def __init__(self, access_token: str):
        """Initialize Bearer token authentication.

        Args:
            access_token: The bearer access token
        """
        if not access_token:
            raise ValueError("access_token is required")

        self.access_token = access_token

    def apply_headers(self) -> dict[str, str]:
        """Apply Bearer token authentication header."""
        return {"Authorization": f"Bearer {self.access_token}"}
