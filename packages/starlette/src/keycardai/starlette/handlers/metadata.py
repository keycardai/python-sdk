"""OAuth metadata endpoint handlers for Starlette/FastAPI.

Implements RFC 9728 (OAuth Protected Resource Metadata) and RFC 8414
(Authorization Server Metadata) discovery endpoints as Starlette handlers.
"""

from collections.abc import Callable

import httpx
from pydantic import AnyHttpUrl, BaseModel, Field

from keycardai.oauth.types.oauth import GrantType, TokenEndpointAuthMethod
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..shared.starlette import get_base_url


class ProtectedResourceMetadata(BaseModel):
    """RFC 9728 OAuth 2.0 Protected Resource Metadata.

    Local model replacing ``mcp.shared.auth.ProtectedResourceMetadata``
    so this package has no MCP dependency.

    See https://datatracker.ietf.org/doc/html/rfc9728#section-2
    """

    resource: AnyHttpUrl | None = Field(default=None)
    authorization_servers: list[AnyHttpUrl] = Field(..., min_length=1)
    jwks_uri: AnyHttpUrl | None = None
    scopes_supported: list[str] | None = None
    bearer_methods_supported: list[str] | None = Field(default=["header"])
    resource_signing_alg_values_supported: list[str] | None = None
    resource_name: str | None = None
    resource_documentation: AnyHttpUrl | None = None
    resource_policy_uri: AnyHttpUrl | None = None
    resource_tos_uri: AnyHttpUrl | None = None
    # Extended fields for OAuth client registration context
    client_id: str | None = Field(default=None)
    client_name: str | None = Field(default=None)
    redirect_uris: list[AnyHttpUrl] | None = Field(default=None)
    token_endpoint_auth_method: TokenEndpointAuthMethod | None = Field(
        default=None
    )
    grant_types: list[GrantType] | None = Field(default=None)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _get_zone_id_from_path(path: str) -> str | None:
    path = path.lstrip("/").rstrip("/")
    zone_id = path.split("/")[0]
    if zone_id == "" or zone_id == "/":
        return None
    return zone_id


def _remove_well_known_prefix(path: str) -> str:
    prefix = ".well-known/oauth-protected-resource"
    path = path.lstrip("/").rstrip("/")
    if path.startswith(prefix):
        return path[len(prefix) :]
    return path


def _create_zone_scoped_authorization_server_url(
    zone_id: str, authorization_server_url: AnyHttpUrl
) -> AnyHttpUrl:
    port_part = (
        f":{authorization_server_url.port}"
        if authorization_server_url.port
        else ""
    )
    url = f"{authorization_server_url.scheme}://{zone_id}.{authorization_server_url.host}{port_part}"
    return AnyHttpUrl(url)


def _create_resource_url(
    base_url: str | AnyHttpUrl, path: str
) -> AnyHttpUrl:
    base_url_str = str(base_url).rstrip("/")
    if path and not path.startswith("/"):
        path = "/" + path
    url = f"{base_url_str}{path}".rstrip("/")
    if url.endswith("://") or (path == "/" and not url.endswith("/")):
        url += "/"
    return AnyHttpUrl(url)


def _create_jwks_uri(base_url: str) -> AnyHttpUrl:
    return AnyHttpUrl(f"{base_url.rstrip('/')}/.well-known/jwks.json")


def _remove_authorization_server_prefix(path: str) -> str:
    auth_server_prefix = "/.well-known/oauth-authorization-server"
    if path.startswith(auth_server_prefix):
        return path[len(auth_server_prefix) :]
    return path


# ---------------------------------------------------------------------------
# Endpoint factories
# ---------------------------------------------------------------------------


def protected_resource_metadata(
    metadata: ProtectedResourceMetadata,
    enable_multi_zone: bool = False,
) -> Callable:
    """Create a Starlette handler that serves OAuth Protected Resource Metadata (RFC 9728)."""

    def wrapper(request: Request) -> Response:
        request_metadata = metadata.model_copy(deep=True)
        path = _remove_well_known_prefix(request.url.path)

        base_url = get_base_url(request)

        if enable_multi_zone:
            zone_id = _get_zone_id_from_path(path)
            if zone_id:
                request_metadata.authorization_servers = [
                    _create_zone_scoped_authorization_server_url(
                        zone_id, request_metadata.authorization_servers[0]
                    )
                ]

        request_metadata.resource = _create_resource_url(base_url, path)
        request_metadata.jwks_uri = _create_jwks_uri(base_url)
        request_metadata.client_id = str(request_metadata.resource)
        request_metadata.client_name = "OAuth Resource Server"
        request_metadata.token_endpoint_auth_method = (
            TokenEndpointAuthMethod.PRIVATE_KEY_JWT
        )
        request_metadata.grant_types = [GrantType.CLIENT_CREDENTIALS]

        return JSONResponse(
            content=request_metadata.model_dump(mode="json", exclude_none=True),
        )

    return wrapper


def authorization_server_metadata(
    issuer: str,
    enable_multi_zone: bool = False,
) -> Callable:
    """Create a Starlette handler that proxies OAuth Authorization Server Metadata (RFC 8414)."""

    def wrapper(request: Request) -> Response:
        actual_issuer = issuer
        try:
            path = _remove_authorization_server_prefix(request.url.path)

            if enable_multi_zone:
                zone_id = _get_zone_id_from_path(path)
                if zone_id:
                    actual_issuer = str(
                        _create_zone_scoped_authorization_server_url(
                            zone_id, AnyHttpUrl(issuer)
                        )
                    )

            issuer_url = str(actual_issuer).rstrip("/")
            # Explicit timeout so a slow upstream cannot pin a Starlette threadpool
            # worker indefinitely. Sync httpx.Client is fine here because Starlette
            # dispatches sync handlers to a threadpool, not the event loop.
            with httpx.Client(timeout=httpx.Timeout(5.0)) as client:
                resp = client.get(
                    f"{issuer_url}/.well-known/oauth-authorization-server"
                )
                resp.raise_for_status()
                return JSONResponse(content=resp.json())
        except httpx.HTTPStatusError as e:
            return JSONResponse(
                content={
                    "error": f"Upstream authorization server returned {e.response.status_code}: {e.response.text}",
                    "type": "upstream_error",
                    "url": str(e.request.url),
                },
                status_code=e.response.status_code,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            return JSONResponse(
                content={
                    "error": f"Unable to connect to authorization server: {str(e)}",
                    "type": "connectivity_error",
                    "url": f"{actual_issuer}/.well-known/oauth-authorization-server",
                },
                status_code=503,
            )
        except Exception as e:
            return JSONResponse(
                content={"error": str(e), "type": type(e).__name__},
                status_code=500,
            )

    return wrapper
