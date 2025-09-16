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


class MultiZoneBasicAuth:
    """Multi-zone HTTP Basic authentication strategy.

    Implements HTTP Basic authentication for multi-zone scenarios where different
    zones require different client credentials. This strategy maintains a mapping
    of zone IDs to their respective client credentials.

    Example:
        ```python
        auth = MultiZoneBasicAuth({
            "zone1": ("client_id_1", "client_secret_1"),
            "zone2": ("client_id_2", "client_secret_2"),
        })

        # Get auth headers for specific zone
        headers = auth.get_headers_for_zone("zone1")
        ```
    """

    def __init__(self, zone_credentials: dict[str, tuple[str, str]]):
        """Initialize multi-zone Basic authentication.

        Args:
            zone_credentials: Dictionary mapping zone IDs to (client_id, client_secret) tuples

        Raises:
            ValueError: If zone_credentials is empty or contains invalid credentials
        """
        if not zone_credentials:
            raise ValueError("zone_credentials cannot be empty")

        self.zone_credentials = {}
        for zone_id, (client_id, client_secret) in zone_credentials.items():
            if not zone_id:
                raise ValueError("zone_id cannot be empty")
            if not client_id:
                raise ValueError(f"client_id is required for zone '{zone_id}'")
            if not client_secret:
                raise ValueError(f"client_secret is required for zone '{zone_id}'")

            self.zone_credentials[zone_id] = BasicAuth(client_id, client_secret)

    def apply_headers(self) -> dict[str, str]:
        """Apply default authentication headers.

        Note: For multi-zone authentication, use get_headers_for_zone() instead.
        This method returns empty headers as zone-specific credentials are required.
        """
        return {}

    def get_headers_for_zone(self, zone_id: str) -> dict[str, str]:
        """Get authentication headers for a specific zone.

        Args:
            zone_id: The zone ID to get credentials for

        Returns:
            Dictionary containing Authorization header for the zone

        Raises:
            KeyError: If zone_id is not configured
        """
        if zone_id not in self.zone_credentials:
            available_zones = list(self.zone_credentials.keys())
            raise KeyError(f"Zone '{zone_id}' not configured. Available zones: {available_zones}")

        return self.zone_credentials[zone_id].apply_headers()

    def has_zone(self, zone_id: str) -> bool:
        """Check if credentials are configured for a zone.

        Args:
            zone_id: The zone ID to check

        Returns:
            True if credentials are configured for the zone, False otherwise
        """
        return zone_id in self.zone_credentials

    def get_configured_zones(self) -> list[str]:
        """Get list of configured zone IDs.

        Returns:
            List of zone IDs that have credentials configured
        """
        return list(self.zone_credentials.keys())

    def get_auth_for_zone(self, zone_id: str) -> "BasicAuth":
        """Get BasicAuth instance for a specific zone.

        Args:
            zone_id: The zone ID to get authentication for

        Returns:
            BasicAuth instance for the zone

        Raises:
            KeyError: If zone_id is not configured
        """
        if zone_id not in self.zone_credentials:
            available_zones = list(self.zone_credentials.keys())
            raise KeyError(f"Zone '{zone_id}' not configured. Available zones: {available_zones}")

        return self.zone_credentials[zone_id]
