"""Authentication strategies for OAuth 2.0 clients.

This module implements various client authentication methods
as defined in OAuth 2.0 specifications.
"""

from abc import ABC, abstractmethod


class ClientAuth(ABC):
    """Base class for OAuth 2.0 client authentication strategies."""

    @abstractmethod
    def get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers for HTTP requests.

        Returns:
            Dictionary of HTTP headers for authentication
        """
        pass

    @abstractmethod
    def get_auth_data(self) -> dict[str, str]:
        """Get authentication data for form body.

        Returns:
            Dictionary of form data for authentication
        """
        pass

    @abstractmethod
    def get_basic_auth(self) -> tuple[str, str] | None:
        """Get basic authentication credentials.

        Returns:
            Tuple of (username, password) or None if not using basic auth
        """
        pass


class ClientSecretBasic(ClientAuth):
    """Client authentication using HTTP Basic authentication.

    As defined in RFC 6749 Section 2.3.1.
    Reference: https://datatracker.ietf.org/doc/html/rfc6749#section-2.3.1
    """

    def __init__(self, client_id: str, client_secret: str):
        """Initialize basic authentication.

        Args:
            client_id: OAuth 2.0 client identifier
            client_secret: OAuth 2.0 client secret
        """
        self.client_id = client_id
        self.client_secret = client_secret

    def get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers."""
        return {}

    def get_auth_data(self) -> dict[str, str]:
        """Get authentication data."""
        return {}

    def get_basic_auth(self) -> tuple[str, str]:
        """Get basic authentication credentials."""
        return (self.client_id, self.client_secret)


class ClientSecretPost(ClientAuth):
    """Client authentication using form body parameters.

    As defined in RFC 6749 Section 2.3.1.
    Reference: https://datatracker.ietf.org/doc/html/rfc6749#section-2.3.1
    """

    def __init__(self, client_id: str, client_secret: str):
        """Initialize form-based authentication.

        Args:
            client_id: OAuth 2.0 client identifier
            client_secret: OAuth 2.0 client secret
        """
        self.client_id = client_id
        self.client_secret = client_secret

    def get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers."""
        return {}

    def get_auth_data(self) -> dict[str, str]:
        """Get authentication data."""
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

    def get_basic_auth(self) -> tuple[str, str] | None:
        """Get basic authentication credentials."""
        return None


class PrivateKeyJWT(ClientAuth):
    """Client authentication using private key JWT.

    As defined in RFC 7523.
    Reference: https://datatracker.ietf.org/doc/html/rfc7523
    """

    def __init__(self, client_id: str, private_key: str, algorithm: str = "RS256"):
        """Initialize JWT-based authentication.

        Args:
            client_id: OAuth 2.0 client identifier
            private_key: Private key for JWT signing (PEM format)
            algorithm: JWT signing algorithm
        """
        self.client_id = client_id
        self.private_key = private_key
        self.algorithm = algorithm

    def get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers."""
        return {}

    def get_auth_data(self) -> dict[str, str]:
        """Get authentication data."""
        # Implementation placeholder - would generate JWT client assertion
        raise NotImplementedError("JWT client assertion generation not yet implemented")

    def get_basic_auth(self) -> tuple[str, str] | None:
        """Get basic authentication credentials."""
        return None


class MutualTLS(ClientAuth):
    """Client authentication using mutual TLS.

    As defined in RFC 8705.
    Reference: https://datatracker.ietf.org/doc/html/rfc8705
    """

    def __init__(self, client_id: str, cert_file: str, key_file: str):
        """Initialize mTLS authentication.

        Args:
            client_id: OAuth 2.0 client identifier
            cert_file: Path to client certificate file
            key_file: Path to private key file
        """
        self.client_id = client_id
        self.cert_file = cert_file
        self.key_file = key_file

    def get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers."""
        return {}

    def get_auth_data(self) -> dict[str, str]:
        """Get authentication data."""
        return {"client_id": self.client_id}

    def get_basic_auth(self) -> tuple[str, str] | None:
        """Get basic authentication credentials."""
        return None
