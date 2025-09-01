"""Authentication strategies for OAuth 2.0 clients.

This module implements various client authentication methods as defined in OAuth 2.0
specifications, with a clean protocol-based design for extensibility.
"""

from typing import Protocol


class AuthStrategy(Protocol):
    """Protocol for OAuth 2.0 client authentication strategies.

    Enables pluggable authentication for different enterprise requirements
    (basic auth, JWT, mTLS, custom auth systems, etc.).
    """

    async def authenticate(self, headers: dict[str, str]) -> dict[str, str]:
        """Apply authentication to request headers.

        Args:
            headers: Existing request headers to augment

        Returns:
            Updated headers dict with authentication applied
        """
        ...

    def get_auth_data(self) -> dict[str, str]:
        """Get authentication data for form body.

        Returns:
            Dictionary of form data for authentication (client_id, client_secret, etc.)
        """
        ...

    def get_basic_auth(self) -> tuple[str, str] | None:
        """Get basic authentication credentials.

        Returns:
            Tuple of (username, password) or None if not using basic auth
        """
        ...


class ClientCredentialsAuth:
    """Client credentials authentication using HTTP Basic or form POST.

    Implements RFC 6749 Section 2.3.1 client authentication methods.
    Default uses Basic auth for security, but supports form POST when needed.
    """

    def __init__(self, client_id: str, client_secret: str, method: str = "basic"):
        """Initialize client credentials authentication.

        Args:
            client_id: OAuth 2.0 client identifier
            client_secret: OAuth 2.0 client secret
            method: Authentication method ('basic' or 'post')
        """
        if not client_id:
            raise ValueError("client_id is required")
        if not client_secret:
            raise ValueError("client_secret is required")
        if method not in ("basic", "post"):
            raise ValueError("method must be 'basic' or 'post'")

        self.client_id = client_id
        self.client_secret = client_secret
        self.method = method

    async def authenticate(self, headers: dict[str, str]) -> dict[str, str]:
        """Apply authentication to request headers."""
        return headers

    def get_auth_data(self) -> dict[str, str]:
        """Get authentication data for form body."""
        if self.method == "post":
            return {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        return {}

    def get_basic_auth(self) -> tuple[str, str] | None:
        """Get basic authentication credentials."""
        if self.method == "basic":
            return (self.client_id, self.client_secret)
        return None


ClientSecretBasic = ClientCredentialsAuth


def ClientSecretPost(client_id: str, client_secret: str) -> ClientCredentialsAuth:
    """Create ClientCredentialsAuth with POST method."""
    return ClientCredentialsAuth(client_id, client_secret, method="post")


class JWTAuth:
    """Client authentication using private key JWT (RFC 7523).

    Used in high-security environments where client secrets are not sufficient.
    """

    def __init__(self, client_id: str, private_key: str, algorithm: str = "RS256"):
        """Initialize JWT-based authentication.

        Args:
            client_id: OAuth 2.0 client identifier
            private_key: Private key for JWT signing (PEM format)
            algorithm: JWT signing algorithm (RS256, ES256, PS256)
        """
        if not client_id:
            raise ValueError("client_id is required")
        if not private_key:
            raise ValueError("private_key is required")

        self.client_id = client_id
        self.private_key = private_key
        self.algorithm = algorithm

    async def authenticate(self, headers: dict[str, str]) -> dict[str, str]:
        """Apply authentication to request headers."""
        return headers

    def get_auth_data(self) -> dict[str, str]:
        """Get authentication data for form body."""
        # TODO: Implement JWT client assertion generation
        # This will be implemented in a later phase with JWT utilities
        raise NotImplementedError(
            "JWT client assertion generation not yet implemented. "
            "Will be added in Phase 2 with enhanced JWT utilities."
        )

    def get_basic_auth(self) -> tuple[str, str] | None:
        """Get basic authentication credentials."""
        return None


class MTLSAuth:
    """Client authentication using mutual TLS (RFC 8705).

    Used in banking/finance and other high-security environments.
    """

    def __init__(self, client_id: str, cert_path: str, key_path: str):
        """Initialize mTLS authentication.

        Args:
            client_id: OAuth 2.0 client identifier
            cert_path: Path to client certificate file
            key_path: Path to private key file
        """
        if not client_id:
            raise ValueError("client_id is required")
        if not cert_path:
            raise ValueError("cert_path is required")
        if not key_path:
            raise ValueError("key_path is required")

        self.client_id = client_id
        self.cert_path = cert_path
        self.key_path = key_path

    async def authenticate(self, headers: dict[str, str]) -> dict[str, str]:
        """Apply authentication to request headers."""
        # mTLS is handled at the TLS layer, not in headers
        return headers

    def get_auth_data(self) -> dict[str, str]:
        """Get authentication data for form body."""
        # mTLS only needs client_id in form body
        return {"client_id": self.client_id}

    def get_basic_auth(self) -> tuple[str, str] | None:
        """Get basic authentication credentials."""
        return None


class NoneAuth:
    """No authentication strategy for unauthenticated clients.

    Used for dynamic client registration and other scenarios where
    the client does not yet have credentials or authentication is
    handled externally (e.g., IP allowlisting, public endpoints).
    """

    def __init__(self):
        """Initialize no-authentication strategy."""
        pass

    async def authenticate(self, headers: dict[str, str]) -> dict[str, str]:
        """Apply authentication to request headers."""
        # No authentication - return headers unchanged
        return headers

    def get_auth_data(self) -> dict[str, str]:
        """Get authentication data for form body."""
        # No authentication data
        return {}

    def get_basic_auth(self) -> tuple[str, str] | None:
        """Get basic authentication credentials."""
        # No basic auth credentials
        return None
