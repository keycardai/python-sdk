"""Authentication strategies for OAuth 2.0 clients.

This module implements HTTP Authorization header strategies for OAuth 2.0
client authentication with a clean protocol-based design.

Strategies receive an optional ``issuer`` selector when headers are applied.
Single-credential strategies (NoneAuth, BasicAuth, BearerAuth) ignore it.
MultiZoneBasicAuth uses it to select the credentials for that issuer and
fails closed when the issuer is missing or not configured.
"""

import base64
from typing import Protocol


def _normalize_issuer(issuer: str) -> str:
    """Normalize an issuer URL for credential map keys and lookups."""
    return issuer.rstrip("/")


class AuthStrategy(Protocol):
    """Protocol for OAuth 2.0 client authentication strategies.

    Defines the interface for setting the Authorization header in HTTP requests.
    All authentication strategies must implement this protocol.
    """

    def apply_headers(self, issuer: str | None = None) -> dict[str, str]:
        """Apply authentication headers to HTTP request.

        Args:
            issuer: Optional issuer URL selecting which credentials to use.
                Strategies holding a single credential ignore it; zone-aware
                strategies require it to resolve per-issuer credentials.

        Returns:
            Dictionary containing Authorization header and any other auth headers
        """
        ...


class NoneAuth:
    """No authentication strategy.

    Used when no authentication is required (e.g., public endpoints,
    dynamic client registration).
    """

    def apply_headers(self, issuer: str | None = None) -> dict[str, str]:
        """Apply no authentication headers. The issuer selector is ignored."""
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

    def apply_headers(self, issuer: str | None = None) -> dict[str, str]:
        """Apply HTTP Basic authentication header. The issuer selector is ignored."""
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

    def apply_headers(self, issuer: str | None = None) -> dict[str, str]:
        """Apply Bearer token authentication header. The issuer selector is ignored."""
        return {"Authorization": f"Bearer {self.access_token}"}


class MultiZoneBasicAuth:
    """Multi-zone HTTP Basic authentication strategy.

    Implements HTTP Basic authentication for multi-zone scenarios where each
    zone requires its own client credentials. Credentials are keyed by the
    zone's issuer URL, which is canonical and self-describing. Trailing
    slashes on issuer URLs are ignored for both storage and lookup.

    Lookups fail closed: requesting credentials for an issuer that is not
    configured raises KeyError, and applying headers without an issuer
    selector raises ValueError. No request is ever sent unauthenticated.

    Example:
        ```python
        auth = MultiZoneBasicAuth({
            "https://zone1.keycard.cloud": ("client_id_1", "client_secret_1"),
            "https://zone2.keycard.cloud": ("client_id_2", "client_secret_2"),
        })

        # Get auth headers for a specific zone issuer
        headers = auth.apply_headers("https://zone1.keycard.cloud")
        ```
    """

    def __init__(self, issuer_credentials: dict[str, tuple[str, str]]):
        """Initialize multi-zone Basic authentication.

        Args:
            issuer_credentials: Dictionary mapping zone issuer URLs to
                (client_id, client_secret) tuples

        Raises:
            ValueError: If issuer_credentials is empty or contains invalid credentials
        """
        if not issuer_credentials:
            raise ValueError("issuer_credentials cannot be empty")

        self.issuer_credentials: dict[str, BasicAuth] = {}
        for issuer, (client_id, client_secret) in issuer_credentials.items():
            if not issuer:
                raise ValueError("issuer cannot be empty")
            if not client_id:
                raise ValueError(f"client_id is required for issuer '{issuer}'")
            if not client_secret:
                raise ValueError(f"client_secret is required for issuer '{issuer}'")

            self.issuer_credentials[_normalize_issuer(issuer)] = BasicAuth(
                client_id, client_secret
            )

    def apply_headers(self, issuer: str | None = None) -> dict[str, str]:
        """Apply HTTP Basic authentication headers for the given issuer.

        Args:
            issuer: The zone issuer URL selecting which credentials to use

        Returns:
            Dictionary containing the Authorization header for the issuer

        Raises:
            ValueError: If issuer is None; multi-zone credentials cannot be
                applied without an issuer selector
            KeyError: If the issuer is not configured
        """
        if issuer is None:
            raise ValueError(
                "MultiZoneBasicAuth requires an issuer to select credentials. "
                "Pass issuer=... on the operation, or use a single-zone "
                "credential strategy."
            )
        return self.get_auth_for_issuer(issuer).apply_headers()

    def has_issuer(self, issuer: str) -> bool:
        """Check if credentials are configured for an issuer.

        Args:
            issuer: The zone issuer URL to check

        Returns:
            True if credentials are configured for the issuer, False otherwise
        """
        return _normalize_issuer(issuer) in self.issuer_credentials

    def get_configured_issuers(self) -> list[str]:
        """Get list of configured zone issuer URLs.

        Returns:
            List of issuer URLs that have credentials configured
        """
        return list(self.issuer_credentials.keys())

    def get_auth_for_issuer(self, issuer: str) -> "BasicAuth":
        """Get BasicAuth instance for a specific zone issuer.

        Args:
            issuer: The zone issuer URL to get authentication for

        Returns:
            BasicAuth instance for the issuer

        Raises:
            KeyError: If the issuer is not configured
        """
        normalized = _normalize_issuer(issuer)
        if normalized not in self.issuer_credentials:
            available_issuers = list(self.issuer_credentials.keys())
            raise KeyError(
                f"Issuer '{issuer}' not configured. Available issuers: {available_issuers}"
            )

        return self.issuer_credentials[normalized]
