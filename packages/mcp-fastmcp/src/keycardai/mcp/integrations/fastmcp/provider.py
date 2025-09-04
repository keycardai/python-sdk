"""KeyCard authentication provider for FastMCP.

This module provides KeycardAuthProvider, which integrates KeyCard's OAuth
token verification with FastMCP's authentication system using the RemoteAuthProvider
pattern for clean separation of concerns.
"""

from __future__ import annotations

from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.utilities.logging import get_logger
from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings

from keycardai.oauth import Client, ClientConfig

logger = get_logger(__name__)


class KeycardTokenVerifier(JWTVerifier):
    """Token verifier for KeyCard zone-issued tokens.

    This verifier automatically discovers KeyCard zone endpoints using the Keycard client,
    extracts the JWKS URI, and uses FastMCP's JWTVerifier for actual token verification.
    This provides the best of both worlds: automatic endpoint discovery and robust JWT validation.
    """

    def __init__(
        self,
        *,
        zone_url: str,
        required_scopes: list[str] | None = None,
        mcp_server_url: AnyHttpUrl | str | None = None,
    ):
        """Initialize the KeyCard token verifier with automatic endpoint discovery.

        Args:
            zone_url: KeyCard zone URL (e.g., "https://abc1234.keycard.cloud")
            required_scopes: Required OAuth scopes for access
            mcp_server_url: URL of the server, usually the public URL of the FastMCP server
        """
        self.zone_url = zone_url.rstrip("/")

        try:
            client_config = ClientConfig(
                enable_metadata_discovery=True,
                auto_register_client=False,
            )

            with Client(
                base_url=self.zone_url,
                config=client_config,
            ) as client:
                metadata = client.discover_server_metadata()

                jwks_uri = metadata.jwks_uri
                issuer = metadata.issuer

                if not jwks_uri:
                    raise ValueError(f"KeyCard zone {self.zone_url} does not provide JWKS URI")


        except Exception as e:
            logger.error("Failed to discover KeyCard zone endpoints: %s", e)
            raise ValueError(f"Failed to discover KeyCard zone endpoints: {e}") from e

        super().__init__(
            jwks_uri=jwks_uri,
            issuer=issuer,
            required_scopes=required_scopes,
            audience=mcp_server_url,
        )



class KeycardAuthProviderSettings(BaseSettings):
    """Settings for KeyCard authentication provider."""

    zone_url: str | None = None
    mcp_server_name: str | None = None
    required_scopes: list[str] | None = None
    mcp_server_url: AnyHttpUrl | str | None = None

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _parse_scopes(cls, v):
        if isinstance(v, str):
            return [scope.strip() for scope in v.split(",") if scope.strip()]
        return v


class KeycardAuthProvider(RemoteAuthProvider):
    """Complete KeyCard authentication provider for FastMCP.

    This provider integrates KeyCard's zone-based authentication with FastMCP's
    authentication system. It uses KeyCard's token introspection endpoint to
    verify tokens and provides RFC 9728 compliant protected resource metadata.

    Features:
    - Token verification via KeyCard zone introspection (RFC 7662)
    - Automatic authorization server discovery from zone
    - RFC 9728 protected resource metadata endpoints
    - Configurable scope requirements
    - Production-ready error handling and logging

    Example:
        ```python
        from fastmcp import FastMCP
        from keycardai.mcp.integrations.fastmcp import KeycardAuthProvider

        auth = KeycardAuthProvider(
            zone_url="https://abc1234.keycard.cloud",
            mcp_server_name="My FastMCP Service",
            required_scopes=["calendar:read", "drive:read"]
        )

        mcp = FastMCP("My Protected Service", auth=auth)
        ```
    """

    def __init__(
        self,
        *,
        zone_url: str,
        mcp_server_name: str | None = None,
        required_scopes: list[str] | None = None,
        mcp_server_url: AnyHttpUrl | str | None = "http://localhost:8000/",
    ):
        """Initialize KeyCard authentication provider.

        Args:
            zone_url: KeyCard zone URL (e.g., "https://abc1234.keycard.cloud")
            mcp_server_name: Human-readable service name for metadata
            required_scopes: Required KeyCard scopes for access
            mcp_server_url: Resource server URL (defaults to FastMCP server URL)
        """
        settings = KeycardAuthProviderSettings.model_validate({
            "zone_url": zone_url,
            "mcp_server_name": mcp_server_name,
            "required_scopes": required_scopes,
            "mcp_server_url": mcp_server_url,
        })

        if not settings.zone_url:
            raise ValueError(
                "zone_url is required - set via parameter or FASTMCP_SERVER_AUTH_KEYCARD_ZONE_URL"
            )

        zone_url_final = settings.zone_url.rstrip("/")
        mcp_server_name_final = settings.mcp_server_name or "FastMCP Service with KeyCard Auth"
        required_scopes_final = settings.required_scopes or []


        token_verifier = KeycardTokenVerifier(
            zone_url=zone_url_final,
            required_scopes=required_scopes_final,
            mcp_server_url=settings.mcp_server_url,
        )

        authorization_servers = [AnyHttpUrl(zone_url_final)]

        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=authorization_servers,
            resource_server_url=settings.mcp_server_url,
            resource_name=mcp_server_name_final,
        )
