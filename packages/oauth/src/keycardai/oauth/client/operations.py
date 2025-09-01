"""OAuth 2.0 client operations for both sync and async clients.

This module contains RFC-compliant implementations of OAuth 2.0 operations
for both synchronous and asynchronous client implementations.
"""

from ..exceptions import ConfigError
from ..types.enums import GrantType, ResponseType, TokenEndpointAuthMethod
from ..types.responses import ClientRegistrationResponse, IntrospectionResponse
from .auth import AuthStrategy


def prepare_introspection_request(
    token: str,
    token_type_hint: str | None,
    auth_strategy: object,
) -> dict[str, str]:
    """Prepare introspection request form data.

    Args:
        token: The token to introspect
        token_type_hint: Optional hint about the type of token
        auth_strategy: Authentication strategy for the request

    Returns:
        Form data for the introspection request

    Raises:
        ValueError: If token is empty
    """
    if not token:
        raise ValueError("token is required")

    form_data = {"token": token}

    if token_type_hint:
        form_data["token_type_hint"] = token_type_hint

    auth_data = auth_strategy.get_auth_data()
    form_data.update(auth_data)

    return form_data


# Async Operations


async def introspect_token_async(
    token: str,
    introspection_endpoint: str,
    auth_strategy: AuthStrategy,
    http_client,  # HTTPClientProtocol
    token_type_hint: str | None = None,
    timeout: float | None = None,
) -> IntrospectionResponse:
    """Introspect an OAuth 2.0 token (async version).

    Implements RFC 7662 token introspection with proper error handling
    and authentication strategy integration.

    Args:
        token: The token to introspect
        introspection_endpoint: Token introspection endpoint URL
        auth_strategy: Authentication strategy for the request
        http_client: HTTP client for making requests
        token_type_hint: Optional hint about the type of token
        timeout: Optional timeout override

    Returns:
        IntrospectionResponse with token metadata and active status

    Raises:
        ConfigError: If introspection endpoint is not configured
        ValueError: If token parameter is empty
        OAuthProtocolError: If server returns OAuth error response
        OAuthHttpError: If HTTP request fails
        NetworkError: If network request fails

    Reference: https://datatracker.ietf.org/doc/html/rfc7662#section-2.1
    """
    if not introspection_endpoint:
        raise ConfigError("Token introspection endpoint not configured")

    if not token:
        raise ValueError("token is required")

    form_data = {"token": token}
    if token_type_hint:
        form_data["token_type_hint"] = token_type_hint

    auth_data = auth_strategy.get_auth_data()
    form_data.update(auth_data)

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    headers.update(await auth_strategy.authenticate(headers))

    response_data = await http_client.request(
        method="POST",
        url=introspection_endpoint,
        data=form_data,
        headers=headers,
        auth=auth_strategy.get_basic_auth(),
        timeout=timeout,
    )

    return IntrospectionResponse.from_response(response_data)


async def register_client_async(
    *,
    client_name: str,
    registration_endpoint: str,
    auth_strategy: AuthStrategy,
    http_client,  # HTTPClientProtocol
    jwks_uri: str | None = None,
    jwks: dict | None = None,
    token_endpoint_auth_method: TokenEndpointAuthMethod
    | str = TokenEndpointAuthMethod.CLIENT_SECRET_BASIC,
    redirect_uris: list[str] | None = None,
    grant_types: list[GrantType | str] | None = None,
    response_types: list[ResponseType | str] | None = None,
    scope: str | list[str] | None = None,
    timeout: float | None = None,
    **additional_metadata,
) -> ClientRegistrationResponse:
    """Register a new OAuth 2.0 client with the authorization server (async version).

    Implements RFC 7591 Dynamic Client Registration with comprehensive validation
    and security best practices.

    Args:
        client_name: Human-readable client name (required)
        registration_endpoint: Client registration endpoint URL
        auth_strategy: Authentication strategy for the request
        http_client: HTTP client for making requests
        jwks_uri: URL pointing to client's JSON Web Key Set
        jwks: Client's JSON Web Key Set (alternative to jwks_uri)
        token_endpoint_auth_method: Client authentication method
        redirect_uris: Client redirect URIs for authorization code flow
        grant_types: OAuth 2.0 grant types the client will use
        response_types: OAuth 2.0 response types the client will use
        scope: Requested scope for the client (string or list)
        timeout: Optional timeout override
        **additional_metadata: Additional client metadata (vendor extensions)

    Returns:
        ClientRegistrationResponse with client credentials and metadata

    Raises:
        ConfigError: If registration endpoint is not configured
        ValueError: If required parameters are missing or invalid
        OAuthProtocolError: If server returns OAuth error response
        OAuthHttpError: If HTTP request fails
        NetworkError: If network request fails

    Reference: https://datatracker.ietf.org/doc/html/rfc7591#section-2
    """
    if not registration_endpoint:
        raise ConfigError("Client registration endpoint not configured")

    if not client_name:
        raise ValueError("client_name is required")

    if isinstance(token_endpoint_auth_method, str):
        token_endpoint_auth_method = TokenEndpointAuthMethod(token_endpoint_auth_method)

    if grant_types:
        grant_types = [
            GrantType(gt) if isinstance(gt, str) else gt for gt in grant_types
        ]

    if response_types:
        response_types = [
            ResponseType(rt) if isinstance(rt, str) else rt for rt in response_types
        ]

    # JWT and private key authentication require key distribution
    secure_auth_methods = [
        TokenEndpointAuthMethod.PRIVATE_KEY_JWT,
        TokenEndpointAuthMethod.CLIENT_SECRET_JWT,
    ]
    if token_endpoint_auth_method in secure_auth_methods and not (jwks_uri or jwks):
        raise ValueError(
            f"jwks_uri or jwks is required for {token_endpoint_auth_method}"
        )

    if isinstance(scope, list):
        scope_str = " ".join(scope)
    else:
        scope_str = scope

    registration_data = {
        "client_name": client_name,
        "token_endpoint_auth_method": token_endpoint_auth_method.value,
    }

    if jwks_uri:
        registration_data["jwks_uri"] = jwks_uri
    if jwks:
        registration_data["jwks"] = jwks
    if redirect_uris:
        registration_data["redirect_uris"] = redirect_uris
    if grant_types:
        registration_data["grant_types"] = [gt.value for gt in grant_types]
    if response_types:
        registration_data["response_types"] = [rt.value for rt in response_types]
    if scope_str:
        registration_data["scope"] = scope_str

    registration_data.update(additional_metadata)

    headers = {"Content-Type": "application/json"}
    headers.update(await auth_strategy.authenticate(headers))
    response_data = await http_client.request(
        method="POST",
        url=registration_endpoint,
        json=registration_data,
        headers=headers,
        auth=auth_strategy.get_basic_auth(),
        timeout=timeout,
    )

    return ClientRegistrationResponse.from_response(response_data)


# Sync Operations


def introspect_token(
    token: str,
    introspection_endpoint: str,
    auth_strategy: AuthStrategy,
    http_client,  # HTTPClient
    token_type_hint: str | None = None,
    timeout: float | None = None,
) -> IntrospectionResponse:
    """Introspect an OAuth 2.0 token (sync version).

    Implements RFC 7662 token introspection with proper error handling
    and authentication strategy integration.

    Args:
        token: The token to introspect
        introspection_endpoint: Token introspection endpoint URL
        auth_strategy: Authentication strategy for the request
        http_client: HTTP client for making requests
        token_type_hint: Optional hint about the type of token
        timeout: Optional timeout override

    Returns:
        IntrospectionResponse with token metadata and active status

    Raises:
        ConfigError: If introspection endpoint is not configured
        ValueError: If token parameter is empty
        OAuthProtocolError: If server returns OAuth error response
        OAuthHttpError: If HTTP request fails
        NetworkError: If network request fails

    Reference: https://datatracker.ietf.org/doc/html/rfc7662#section-2.1
    """
    if not introspection_endpoint:
        raise ConfigError("Token introspection endpoint not configured")

    form_data = prepare_introspection_request(token, token_type_hint, auth_strategy)

    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    response_data = http_client.request(
        method="POST",
        url=introspection_endpoint,
        data=form_data,
        headers=headers,
        auth=auth_strategy.get_basic_auth(),
        timeout=timeout,
    )

    return IntrospectionResponse.from_response(response_data)


def register_client(
    *,
    client_name: str,
    registration_endpoint: str,
    auth_strategy: AuthStrategy,
    http_client,  # HTTPClient
    jwks_uri: str | None = None,
    jwks: dict | None = None,
    token_endpoint_auth_method: TokenEndpointAuthMethod
    | str = TokenEndpointAuthMethod.CLIENT_SECRET_BASIC,
    redirect_uris: list[str] | None = None,
    grant_types: list[GrantType | str] | None = None,
    response_types: list[ResponseType | str] | None = None,
    scope: str | list[str] | None = None,
    timeout: float | None = None,
    **additional_metadata,
) -> ClientRegistrationResponse:
    """Register a new OAuth 2.0 client with the authorization server (sync version).

    Implements RFC 7591 Dynamic Client Registration with comprehensive validation
    and security best practices.

    Args:
        client_name: Human-readable client name (required)
        registration_endpoint: Client registration endpoint URL
        auth_strategy: Authentication strategy for the request
        http_client: HTTP client for making requests
        jwks_uri: URL pointing to client's JSON Web Key Set
        jwks: Client's JSON Web Key Set (alternative to jwks_uri)
        token_endpoint_auth_method: Client authentication method
        redirect_uris: Client redirect URIs for authorization code flow
        grant_types: OAuth 2.0 grant types the client will use
        response_types: OAuth 2.0 response types the client will use
        scope: Requested scope for the client (string or list)
        timeout: Optional timeout override
        **additional_metadata: Additional client metadata (vendor extensions)

    Returns:
        ClientRegistrationResponse with client credentials and metadata

    Raises:
        ConfigError: If registration endpoint is not configured
        ValueError: If required parameters are missing or invalid
        OAuthProtocolError: If server returns OAuth error response
        OAuthHttpError: If HTTP request fails
        NetworkError: If network request fails

    Reference: https://datatracker.ietf.org/doc/html/rfc7591#section-2
    """
    if not registration_endpoint:
        raise ConfigError("Client registration endpoint not configured")

    if not client_name:
        raise ValueError("client_name is required")

    if isinstance(token_endpoint_auth_method, str):
        token_endpoint_auth_method = TokenEndpointAuthMethod(token_endpoint_auth_method)

    if grant_types:
        grant_types = [
            GrantType(gt) if isinstance(gt, str) else gt for gt in grant_types
        ]

    if response_types:
        response_types = [
            ResponseType(rt) if isinstance(rt, str) else rt for rt in response_types
        ]

    # JWT and private key authentication require key distribution
    secure_auth_methods = [
        TokenEndpointAuthMethod.PRIVATE_KEY_JWT,
        TokenEndpointAuthMethod.CLIENT_SECRET_JWT,
    ]
    if token_endpoint_auth_method in secure_auth_methods and not (jwks_uri or jwks):
        raise ValueError(
            f"jwks_uri or jwks is required for {token_endpoint_auth_method}"
        )

    if isinstance(scope, list):
        scope_str = " ".join(scope)
    else:
        scope_str = scope

    registration_data = {
        "client_name": client_name,
        "token_endpoint_auth_method": token_endpoint_auth_method.value,
    }

    if jwks_uri:
        registration_data["jwks_uri"] = jwks_uri
    if jwks:
        registration_data["jwks"] = jwks
    if redirect_uris:
        registration_data["redirect_uris"] = redirect_uris
    if grant_types:
        registration_data["grant_types"] = [gt.value for gt in grant_types]
    if response_types:
        registration_data["response_types"] = [rt.value for rt in response_types]
    if scope_str:
        registration_data["scope"] = scope_str

    registration_data.update(additional_metadata)

    headers = {"Content-Type": "application/json"}

    response_data = http_client.request(
        method="POST",
        url=registration_endpoint,
        json=registration_data,
        headers=headers,
        auth=auth_strategy.get_basic_auth(),
        timeout=timeout,
    )

    return ClientRegistrationResponse.from_response(response_data)


# Future operations will be implemented in later phases
class TokenRevocationClient:
    """OAuth 2.0 Token Revocation Client (RFC 7009).

    Will be implemented in Phase 2 with comprehensive revocation support.
    """

    async def revoke_token_async(
        self,
        token: str,
        auth_strategy: AuthStrategy,
        http_client,
        token_type_hint: str | None = None,
        *,
        timeout: float | None = None,
    ) -> None:
        """Revoke an OAuth 2.0 token (async version).

        Args:
            token: The token to revoke
            auth_strategy: Authentication strategy
            http_client: HTTP client for requests
            token_type_hint: Hint about token type (access_token, refresh_token)
            timeout: Optional timeout override

        Raises:
            OAuthProtocolError: If server returns OAuth error response
            OAuthHttpError: If HTTP request fails
            OAuthRequestError: If the HTTP request fails

        Reference: https://datatracker.ietf.org/doc/html/rfc7009#section-2
        """
        raise NotImplementedError("Token revocation not yet implemented")


class PushedAuthorizationClient:
    """OAuth 2.0 Pushed Authorization Requests Client (RFC 9126).

    Will be implemented in Phase 2 with PAR support.
    """

    async def push_authorization_request_async(
        self,
        auth_strategy: AuthStrategy,
        http_client,
        *,
        client_id: str,
        redirect_uri: str,
        response_type: str = "code",
        scope: str | None = None,
        state: str | None = None,
        code_challenge: str | None = None,
        code_challenge_method: str | None = None,
        timeout: float | None = None,
        **additional_params,
    ) -> dict[str, str]:
        """Push authorization request parameters to the authorization server (async version).

        Args:
            auth_strategy: Authentication strategy
            http_client: HTTP client for requests
            client_id: Client identifier
            redirect_uri: Client redirect URI
            response_type: OAuth 2.0 response type
            scope: Requested scope
            state: State parameter for CSRF protection
            code_challenge: PKCE code challenge
            code_challenge_method: PKCE code challenge method
            timeout: Optional timeout override
            **additional_params: Additional authorization parameters

        Returns:
            Dictionary containing request_uri and expires_in

        Raises:
            OAuthProtocolError: If server returns OAuth error response
            OAuthInvalidClientError: If client authentication fails
            OAuthRequestError: If the HTTP request fails

        Reference: https://datatracker.ietf.org/doc/html/rfc9126#section-2
        """
        raise NotImplementedError("Pushed authorization requests not yet implemented")
