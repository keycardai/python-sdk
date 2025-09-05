"""OAuth 2.0 authorization server metadata discovery operations.

This module implements RFC 8414 authorization server metadata discovery
using the new HTTP transport layer with byte-level operations.
"""

import json

from ..exceptions import ConfigError, OAuthHttpError, OAuthProtocolError
from ..http._context import HTTPContext
from ..http._wire import HttpRequest, HttpResponse
from ..types.models import AuthorizationServerMetadata, ServerMetadataRequest
from ..types.oauth import WellKnownEndpoint
from ._exec import execute_async, execute_sync


def _validate_discovery_params(base_url: str) -> None:
    """Validate discovery parameters.

    Args:
        base_url: The base URL to validate

    Raises:
        ConfigError: If base_url is empty or invalid
    """
    if not base_url or not base_url.strip():
        raise ConfigError("base_url cannot be empty")

def _build_server_metadata_request_from_kwargs(client_base_url: str, **kwargs) -> ServerMetadataRequest:
    """Build a ServerMetadataRequest from keyword arguments.

    Args:
        client_base_url: Default base URL from the client
        **kwargs: Keyword arguments matching ServerMetadataRequest fields

    Returns:
        ServerMetadataRequest built from the provided kwargs
    """
    # Use provided base_url or fall back to client's base_url
    base_url = kwargs.get("base_url", client_base_url)

    return ServerMetadataRequest(base_url=base_url)


def build_discovery_http_request(
    request: ServerMetadataRequest, auth_headers: dict[str, str] | None = None
) -> HttpRequest:
    """Build HTTP request for server metadata discovery.

    Args:
        request: Server metadata discovery request
        auth_headers: Optional authentication headers

    Returns:
        HttpRequest for the discovery endpoint

    Raises:
        ConfigError: If base_url is invalid
    """
    _validate_discovery_params(request.base_url)

    # Construct discovery URL according to RFC 8414 Section 3
    # Format: {issuer}/.well-known/oauth-authorization-server
    discovery_url = WellKnownEndpoint.construct_url(
        request.base_url,
        WellKnownEndpoint.OAUTH_AUTHORIZATION_SERVER
    )

    headers = {
        "Accept": "application/json",
        "User-Agent": "KeyCardAI-OAuth/0.0.1"
    }
    if auth_headers:
        headers.update(auth_headers)

    return HttpRequest(
        method="GET",
        url=discovery_url,
        headers=headers,
        body=None  # GET request has no body
    )


def parse_discovery_http_response(res: HttpResponse) -> AuthorizationServerMetadata:
    """Parse HTTP response from server metadata discovery.

    Args:
        res: HTTP response from discovery endpoint

    Returns:
        AuthorizationServerMetadata with discovered server capabilities

    Raises:
        OAuthHttpError: If HTTP error status
        OAuthProtocolError: If invalid response format
    """
    # TODO: Handle errors more granularly
    if res.status >= 400:
        response_body = res.body[:512].decode("utf-8", "ignore")
        raise OAuthHttpError(
            status_code=res.status,
            response_body=response_body,
            headers=dict(res.headers),
            operation="GET /.well-known/oauth-authorization-server"
        )

    try:
        data = json.loads(res.body.decode("utf-8"))
    except Exception as e:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description="Invalid JSON in discovery response",
            operation="GET /.well-known/oauth-authorization-server"
        ) from e

    if isinstance(data, dict) and "error" in data:
        raise OAuthProtocolError(
            error=data["error"],
            error_description=data.get("error_description"),
            error_uri=data.get("error_uri"),
            operation="GET /.well-known/oauth-authorization-server"
        )

    if "issuer" not in data:
        raise ValueError("Authorization server metadata must include 'issuer' field")

    def normalize_array_field(field_name: str) -> list[str] | None:
        value = data.get(field_name)
        if isinstance(value, str):
            return value.split() if value else None
        elif isinstance(value, list):
            return value if value else None
        return None

    return AuthorizationServerMetadata(
        issuer=data["issuer"],

        authorization_endpoint=data.get("authorization_endpoint"),
        token_endpoint=data.get("token_endpoint"),
        introspection_endpoint=data.get("introspection_endpoint"),
        revocation_endpoint=data.get("revocation_endpoint"),
        registration_endpoint=data.get("registration_endpoint"),
        pushed_authorization_request_endpoint=data.get("pushed_authorization_request_endpoint"),
        jwks_uri=data.get("jwks_uri"),

        response_types_supported=normalize_array_field("response_types_supported"),
        response_modes_supported=normalize_array_field("response_modes_supported"),
        grant_types_supported=normalize_array_field("grant_types_supported"),
        subject_types_supported=normalize_array_field("subject_types_supported"),
        scopes_supported=normalize_array_field("scopes_supported"),

        token_endpoint_auth_methods_supported=normalize_array_field("token_endpoint_auth_methods_supported"),
        token_endpoint_auth_signing_alg_values_supported=normalize_array_field("token_endpoint_auth_signing_alg_values_supported"),
        introspection_endpoint_auth_methods_supported=normalize_array_field("introspection_endpoint_auth_methods_supported"),
        introspection_endpoint_auth_signing_alg_values_supported=normalize_array_field("introspection_endpoint_auth_signing_alg_values_supported"),
        revocation_endpoint_auth_methods_supported=normalize_array_field("revocation_endpoint_auth_methods_supported"),
        revocation_endpoint_auth_signing_alg_values_supported=normalize_array_field("revocation_endpoint_auth_signing_alg_values_supported"),

        code_challenge_methods_supported=normalize_array_field("code_challenge_methods_supported"),

        service_documentation=data.get("service_documentation"),
        ui_locales_supported=normalize_array_field("ui_locales_supported"),
        op_policy_uri=data.get("op_policy_uri"),
        op_tos_uri=data.get("op_tos_uri"),

        # Preserve raw response and headers
        raw=data,
        headers=dict(res.headers),
    )

def discover_server_metadata(
    request: ServerMetadataRequest,
    context: HTTPContext,
) -> AuthorizationServerMetadata:
    """Discover OAuth 2.0 authorization server metadata (sync version).

    Implements RFC 8414 authorization server metadata discovery with automatic
    endpoint URL construction and graceful error handling using the new HTTP transport layer.

    Args:
        request: Server metadata discovery request with base_url
        context: Operation context with transport and configuration

    Returns:
        AuthorizationServerMetadata with discovered server capabilities

    Raises:
        ConfigError: If base_url is empty
        OAuthHttpError: If discovery endpoint is unreachable or returns non-200
        OAuthProtocolError: If metadata format is invalid or missing required fields
        NetworkError: If network request fails

    Reference: https://datatracker.ietf.org/doc/html/rfc8414#section-3
    """
    # Build HTTP request
    auth_headers = None
    if hasattr(context, 'auth') and context.auth:
        auth_headers = dict(context.auth.apply_headers())

    http_req = build_discovery_http_request(request, auth_headers)

    # Execute HTTP request using transport
    http_res = execute_sync(context.transport, http_req, context.timeout)

    # Parse and return metadata
    return parse_discovery_http_response(http_res)


async def discover_server_metadata_async(
    request: ServerMetadataRequest,
    context: HTTPContext,
) -> AuthorizationServerMetadata:
    """Discover OAuth 2.0 authorization server metadata (async version).

    Implements RFC 8414 authorization server metadata discovery with automatic
    endpoint URL construction and graceful error handling using the new HTTP transport layer.

    Args:
        request: Server metadata discovery request with base_url
        context: Operation context with transport and configuration

    Returns:
        AuthorizationServerMetadata with discovered server capabilities

    Raises:
        ConfigError: If base_url is empty
        OAuthHttpError: If discovery endpoint is unreachable or returns non-200
        OAuthProtocolError: If metadata format is invalid or missing required fields
        NetworkError: If network request fails

    Reference: https://datatracker.ietf.org/doc/html/rfc8414#section-3
    """
    auth_headers = dict(context.auth.apply_headers())

    http_req = build_discovery_http_request(request, auth_headers)

    # Execute HTTP request using async transport
    http_res = await execute_async(context.transport, http_req, context.timeout)

    # Parse and return metadata
    return parse_discovery_http_response(http_res)
