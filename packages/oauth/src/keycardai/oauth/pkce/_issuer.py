"""Shared authorization-server issuer resolution for PKCE flows."""

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

    from .client import resolve_issuer_from_challenge

    return await resolve_issuer_from_challenge(
        www_authenticate_header, http_client=http_client
    )
