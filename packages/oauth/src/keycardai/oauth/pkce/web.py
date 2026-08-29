"""Stateless web-application authorization-code flow with PKCE.

The web-app flow separates authorization into a begin step and a complete
step around the application's own redirect route. The application stores the
returned ``state`` and ``code_verifier`` between those calls.
"""

import secrets
from collections.abc import Mapping

import httpx
from pydantic import BaseModel

from ..client import AsyncClient
from ..exceptions import (
    AuthorizationDeniedError,
    ConfigError,
    OAuthProtocolError,
    StateMismatchError,
)
from ..http.auth import BasicAuth, NoneAuth
from ..operations._authorize import build_authorize_url
from ..types.models import (
    AuthorizationServerMetadata,
    ClientConfig,
    Endpoints,
    TokenResponse,
)
from ..utils.pkce import PKCEGenerator
from ._issuer import resolve_issuer_from_challenge


class AuthorizationRedirect(BaseModel):
    """Authorization redirect URL and values the application must retain.

    Attributes:
        url: The authorization URL to which the browser should be redirected.
        state: The generated CSRF value to store until the callback.
        code_verifier: The PKCE verifier to store until the callback. This
            value must never be sent to the browser.
        resources: The resources the authorization request was scoped to,
            carried so the application can persist them alongside ``state``
            and ``code_verifier``. They are not needed to redeem the code —
            the authorization server derives the issued token's audience from
            the code itself — but applications commonly need to know which
            resources a session was authorized for.
    """

    url: str
    state: str
    code_verifier: str
    resources: list[str] | None = None


async def begin_authorization(
    *,
    client_id: str,
    redirect_uri: str,
    resources: list[str] | None = None,
    www_authenticate_header: str | None = None,
    issuer: str | None = None,
    metadata: AuthorizationServerMetadata | None = None,
    scopes: list[str] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> AuthorizationRedirect:
    """Begin a web-app authorization-code-with-PKCE flow.

    Returns the URL to which the application should redirect the user's
    browser, together with the ``state`` and ``code_verifier`` to store until
    the callback route is reached.

    Args:
        client_id: OAuth client ID.
        redirect_uri: Registered redirect URI handled by the web application.
        resources: The protected resources the caller is targeting. Each entry
            is sent as its own RFC 8707 ``resource`` query parameter, so a
            single authorization can cover several resources and the issued
            token's audience covers all of them.
        www_authenticate_header: The ``WWW-Authenticate`` challenge from the
            protected resource. Must contain a ``resource_metadata`` URL per
            RFC 9728. Mutually exclusive with ``issuer``.
        issuer: Authorization server issuer URL to use directly. Mutually
            exclusive with ``www_authenticate_header``.
        metadata: Optional pre-discovered authorization server metadata.
            Discovery is skipped when provided, and the application owns
            caching and refreshing this metadata.
        scopes: Optional list of OAuth scopes to request.
        http_client: Optional ``httpx.AsyncClient`` used to fetch protected
            resource metadata in challenge-driven mode. When omitted, a
            short-lived client is created internally.

    Returns:
        ``AuthorizationRedirect`` containing the authorization URL, generated
        state, PKCE code verifier, and the requested resources. Store the
        state and verifier in application-controlled session state.

    Raises:
        keycardai.oauth.ConfigError: If anything other than exactly one of
            ``issuer``, ``www_authenticate_header``, or ``metadata`` is
            provided, or challenge mode omits ``resources``.
        ValueError: If the authorization endpoint is missing from the
            supplied metadata or discovered server metadata, or if challenge
            discovery metadata is incomplete.
        httpx.HTTPStatusError: If fetching protected resource metadata fails.
        keycardai.oauth.OAuthHttpError: If authorization server discovery
            returns an HTTP error.
        keycardai.oauth.OAuthProtocolError: If authorization server discovery
            returns an OAuth protocol error.
    """
    _validate_entry_mode(
        issuer=issuer,
        www_authenticate_header=www_authenticate_header,
        metadata=metadata,
    )

    if metadata is not None:
        if metadata.authorization_endpoint is None:
            raise ValueError(
                "Authorization server metadata is missing authorization_endpoint"
            )
        authorization_endpoint = metadata.authorization_endpoint
    else:
        if www_authenticate_header is not None and not resources:
            raise ConfigError(
                "'resources' is required when authenticating from a "
                "WWW-Authenticate challenge"
            )
        auth_server_url = await _resolve_issuer(
            issuer=issuer,
            www_authenticate_header=www_authenticate_header,
            http_client=http_client,
        )
        config = ClientConfig(
            enable_metadata_discovery=True, auto_register_client=False
        )

        async with AsyncClient(
            issuer=auth_server_url, auth=NoneAuth(), config=config
        ) as oauth_client:
            endpoints = await oauth_client.get_endpoints()
            if not endpoints.authorize:
                raise ValueError(
                    "Authorization server metadata is missing authorization_endpoint"
                )
            authorization_endpoint = endpoints.authorize

    pkce = PKCEGenerator().generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    url = build_authorize_url(
        authorization_endpoint,
        client_id=client_id,
        redirect_uri=redirect_uri,
        pkce=pkce,
        resources=resources,
        scope=" ".join(scopes) if scopes else None,
        state=state,
    )

    return AuthorizationRedirect(
        url=url,
        state=state,
        code_verifier=pkce.code_verifier,
        resources=resources,
    )


async def complete_authorization(
    *,
    callback_params: Mapping[str, str],
    state: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
    www_authenticate_header: str | None = None,
    issuer: str | None = None,
    metadata: AuthorizationServerMetadata | None = None,
    client_secret: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> TokenResponse:
    """Complete a web-app authorization-code-with-PKCE flow.

    Callback validation occurs before issuer discovery or any token request.
    The application supplies the ``state`` and ``code_verifier`` retained
    from :func:`begin_authorization`.

    No RFC 8707 ``resource`` parameter is sent on the token request: the
    authorization server derives the issued token's audience from the
    authorization code, which already records the resources the user
    authorized in the begin step.

    Args:
        callback_params: Query parameters received by the application's
            callback route, including ``code`` and ``state`` or an OAuth
            ``error`` and optional ``error_description``.
        state: The state value stored from the begin step.
        code_verifier: The PKCE verifier stored from the begin step. This
            value must never be sent to the browser.
        client_id: OAuth client ID.
        redirect_uri: The same registered redirect URI used in the begin step.
        www_authenticate_header: The ``WWW-Authenticate`` challenge from the
            protected resource. Must contain a ``resource_metadata`` URL per
            RFC 9728. Mutually exclusive with ``issuer``.
        issuer: Authorization server issuer URL to use directly. Mutually
            exclusive with ``www_authenticate_header``.
        metadata: Optional pre-discovered authorization server metadata.
            Discovery is skipped when provided, and the application owns
            caching and refreshing this metadata.
        client_secret: Optional client secret for confidential clients.
            Public clients omit this and use no token-endpoint auth.
        http_client: Optional ``httpx.AsyncClient`` used to fetch protected
            resource metadata in challenge-driven mode. When omitted, a
            short-lived client is created internally.

    Returns:
        ``TokenResponse`` returned by the authorization server's token
        endpoint.

    Raises:
        keycardai.oauth.ConfigError: If anything other than exactly one of
            ``issuer``, ``www_authenticate_header``, or ``metadata`` is
            provided.
        ValueError: If the token endpoint is missing from the supplied
            metadata or discovered server metadata, or if challenge discovery
            metadata is incomplete.
        AuthorizationDeniedError: If the callback carries an OAuth
            authorization error. No token request is made.
        StateMismatchError: If the callback state is missing or does not match
            the stored state. No token request is made.
        OAuthProtocolError: If the callback has no authorization code, or if
            the token endpoint returns an OAuth protocol error.
        httpx.HTTPStatusError: If fetching protected resource metadata fails.
        keycardai.oauth.OAuthHttpError: If authorization server discovery or
            the token endpoint returns an HTTP error.
    """
    error = callback_params.get("error")
    if error is not None:
        raise AuthorizationDeniedError(
            error=error,
            error_description=callback_params.get("error_description"),
            operation="authorization callback",
        )

    callback_state = callback_params.get("state")
    if callback_state is None or not secrets.compare_digest(
        callback_state.encode("utf-8"), state.encode("utf-8")
    ):
        raise StateMismatchError()

    code = callback_params.get("code")
    if code is None:
        raise OAuthProtocolError(
            error="invalid_request",
            error_description="Authorization callback is missing 'code'",
            operation="authorization callback",
        )

    _validate_entry_mode(
        issuer=issuer,
        www_authenticate_header=www_authenticate_header,
        metadata=metadata,
    )
    if metadata is not None:
        if metadata.token_endpoint is None:
            raise ValueError(
                "Authorization server metadata is missing token_endpoint"
            )
        auth_server_url = metadata.issuer
    else:
        auth_server_url = await _resolve_issuer(
            issuer=issuer,
            www_authenticate_header=www_authenticate_header,
            http_client=http_client,
        )

    auth_strategy = (
        BasicAuth(client_id, client_secret) if client_secret else NoneAuth()
    )
    config = ClientConfig(
        enable_metadata_discovery=metadata is None, auto_register_client=False
    )

    async with AsyncClient(
        issuer=auth_server_url,
        auth=auth_strategy,
        config=config,
        endpoints=Endpoints(token=metadata.token_endpoint) if metadata else None,
    ) as oauth_client:
        if metadata is None:
            endpoints = await oauth_client.get_endpoints()
            if not endpoints.token:
                raise ValueError(
                    "Authorization server metadata is missing token_endpoint"
                )
        return await oauth_client.exchange_authorization_code(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            client_id=client_id,
        )



async def _resolve_issuer(
    *,
    issuer: str | None,
    www_authenticate_header: str | None,
    http_client: httpx.AsyncClient | None,
) -> str:
    if issuer is not None:
        return issuer.rstrip("/")
    if www_authenticate_header is None:
        raise ConfigError(
            "Provide exactly one of 'issuer', 'www_authenticate_header', "
            "or 'metadata'"
        )
    return await resolve_issuer_from_challenge(
        www_authenticate_header, http_client=http_client
    )


def _validate_entry_mode(
    *,
    issuer: str | None,
    www_authenticate_header: str | None,
    metadata: AuthorizationServerMetadata | None,
) -> None:
    if sum(
        value is not None
        for value in (issuer, www_authenticate_header, metadata)
    ) != 1:
        raise ConfigError(
            "Provide exactly one of 'issuer', 'www_authenticate_header', "
            "or 'metadata'"
        )
