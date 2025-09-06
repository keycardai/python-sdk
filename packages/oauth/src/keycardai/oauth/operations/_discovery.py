"""OAuth 2.0 authorization server metadata discovery operations.

This module implements RFC 8414 authorization server metadata discovery
using the new HTTP transport layer with byte-level operations.
"""

import json

from ..exceptions import OAuthHttpError, OAuthProtocolError
from ..http._context import HTTPContext
from ..http._wire import HttpRequest, HttpResponse
from ..types.models import AuthorizationServerMetadata, ServerMetadataRequest
from ..types.oauth import WellKnownEndpoint


def build_discovery_http_request(
    request: ServerMetadataRequest, context: HTTPContext
) -> HttpRequest:
    """Build HTTP request for server metadata discovery.

    Args:
        request: Server metadata discovery request
        context: HTTP context with transport, auth, and headers

    Returns:
        HttpRequest for the discovery endpoint

    Raises:
        ConfigError: If base_url is invalid
    """
    # Construct discovery URL according to RFC 8414 Section 3
    # Format: {issuer}/.well-known/oauth-authorization-server
    discovery_url = WellKnownEndpoint.construct_url(
        request.base_url,
        WellKnownEndpoint.OAUTH_AUTHORIZATION_SERVER
    )

    headers = {
        "Accept": "application/json",
    }
    if context.headers:
        headers.update(context.headers)

    if context.auth:
        headers.update(dict(context.auth.apply_headers()))

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
    http_req = build_discovery_http_request(request, context)
    http_res = context.transport.request_raw(http_req, timeout=context.timeout)
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
    http_req = build_discovery_http_request(request, context)
    http_res = await context.transport.request_raw(http_req, timeout=context.timeout)
    return parse_discovery_http_response(http_res)
