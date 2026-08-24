"""Shared authorization-server issuer resolution for PKCE flows."""

import re
from typing import Any

import httpx

from ..exceptions import ConfigError


async def _resolve_auth_server_url(
    *,
    issuer: str | None,
    www_authenticate_header: str | None,
    resource_url: str | None,
    http_client: httpx.AsyncClient | None,
) -> str:
    """Resolve the authorization server from the supported flow entry modes."""
    if issuer is not None:
        if www_authenticate_header is not None:
            raise ConfigError(
                "Provide exactly one of 'issuer' or 'www_authenticate_header' "
                "to resolve the authorization server"
            )
        return issuer.rstrip("/")

    if www_authenticate_header is None:
        raise ConfigError(
            "Provide exactly one of 'issuer' or 'www_authenticate_header' "
            "to resolve the authorization server"
        )
    if resource_url is None:
        raise ConfigError(
            "'resource_url' is required when authenticating from a "
            "WWW-Authenticate challenge"
        )

    return await resolve_issuer_from_challenge(
        www_authenticate_header, http_client=http_client
    )


async def resolve_issuer_from_challenge(
    www_authenticate_header: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Resolve the authorization server issuer from a ``WWW-Authenticate`` challenge.

    Parses the ``resource_metadata`` URL from the challenge (RFC 9728),
    fetches the protected resource metadata document, and returns the first
    entry of ``authorization_servers`` with any trailing slash removed.

    Args:
        www_authenticate_header: The ``WWW-Authenticate`` value from the
            protected resource's 401 response.
        http_client: Optional ``httpx.AsyncClient`` used to fetch the
            protected resource metadata document. When not supplied, a
            short-lived client is created internally.

    Returns:
        The issuer URL of the resource's first advertised authorization
        server.

    Raises:
        ValueError: If the challenge has no ``resource_metadata`` URL or the
            metadata document lists no ``authorization_servers``.
        httpx.HTTPStatusError: If the resource metadata fetch fails.
    """
    metadata_url = _extract_resource_metadata_url(www_authenticate_header)
    if not metadata_url:
        raise ValueError("No resource_metadata URL in WWW-Authenticate header")

    resource_metadata = await _fetch_resource_metadata(metadata_url, http_client)
    auth_servers = resource_metadata.get("authorization_servers") or []
    if not auth_servers:
        raise ValueError("No authorization_servers in resource metadata")

    return str(auth_servers[0]).rstrip("/")


async def _fetch_resource_metadata(
    metadata_url: str, http_client: httpx.AsyncClient | None
) -> dict[str, Any]:
    """Fetch the RFC 9728 protected resource metadata document.

    This step is paired with the protected resource (not the OAuth server),
    so it lives outside :class:`AsyncClient`.
    """
    if http_client is not None:
        response = await http_client.get(metadata_url)
        response.raise_for_status()
        return response.json()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(metadata_url)
        response.raise_for_status()
        return response.json()


def _extract_resource_metadata_url(www_authenticate: str) -> str | None:
    """Extract the ``resource_metadata`` URL from a ``WWW-Authenticate`` header.

    See RFC 9728 §5.3 for the parameter definition.
    """
    match = re.search(r'resource_metadata="([^"]+)"', www_authenticate)
    return match.group(1) if match else None
