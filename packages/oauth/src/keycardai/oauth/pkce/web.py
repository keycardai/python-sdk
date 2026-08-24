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
    OAuthProtocolError,
    StateMismatchError,
)
from ..http.auth import BasicAuth, NoneAuth
from ..operations._authorize import build_authorize_url
from ..types.models import ClientConfig, TokenResponse
from ..utils.pkce import PKCEGenerator
from .client import _resolve_auth_server_url


class AuthorizationRedirect(BaseModel):
    """Authorization redirect URL and values the application must retain."""

    url: str
    state: str
    code_verifier: str


async def begin_authorization(
    *,
    client_id: str,
    redirect_uri: str,
    resource_url: str | None = None,
    www_authenticate_header: str | None = None,
    issuer: str | None = None,
    scopes: list[str] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> AuthorizationRedirect:
    """Begin a web-app authorization-code-with-PKCE flow.

    Returns the URL to which the application should redirect the user's
    browser, together with the ``state`` and ``code_verifier`` to store until
    the callback route is reached.
    """
    auth_server_url = await _resolve_auth_server_url(
        issuer=issuer,
        www_authenticate_header=www_authenticate_header,
        resource_url=resource_url,
        http_client=http_client,
    )
    auth_strategy = NoneAuth()
    config = ClientConfig(enable_metadata_discovery=True, auto_register_client=False)

    async with AsyncClient(
        issuer=auth_server_url, auth=auth_strategy, config=config
    ) as oauth_client:
        endpoints = await oauth_client.get_endpoints()
        if not endpoints.authorize or not endpoints.token:
            raise ValueError(
                "Authorization server metadata is missing authorization_endpoint "
                "or token_endpoint"
            )

        pkce = PKCEGenerator().generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        url = build_authorize_url(
            endpoints.authorize,
            client_id=client_id,
            redirect_uri=redirect_uri,
            pkce=pkce,
            resources=[resource_url] if resource_url else None,
            scope=" ".join(scopes) if scopes else None,
            state=state,
        )

    return AuthorizationRedirect(
        url=url,
        state=state,
        code_verifier=pkce.code_verifier,
    )


async def complete_authorization(
    *,
    callback_params: Mapping[str, str],
    state: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
    resource_url: str | None = None,
    www_authenticate_header: str | None = None,
    issuer: str | None = None,
    client_secret: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> TokenResponse:
    """Complete a web-app authorization-code-with-PKCE flow.

    Callback validation occurs before issuer discovery or any token request.
    The application supplies the ``state`` and ``code_verifier`` retained
    from :func:`begin_authorization`.
    """
    error = callback_params.get("error")
    if error is not None:
        raise AuthorizationDeniedError(
            error=error,
            error_description=callback_params.get("error_description"),
            operation="authorization callback",
        )

    callback_state = callback_params.get("state")
    if callback_state is None or not secrets.compare_digest(callback_state, state):
        raise StateMismatchError()

    code = callback_params.get("code")
    if code is None:
        raise OAuthProtocolError(
            error="invalid_request",
            error_description="Authorization callback is missing 'code'",
            operation="authorization callback",
        )

    auth_server_url = await _resolve_auth_server_url(
        issuer=issuer,
        www_authenticate_header=www_authenticate_header,
        resource_url=resource_url,
        http_client=http_client,
    )
    auth_strategy = (
        BasicAuth(client_id, client_secret) if client_secret else NoneAuth()
    )
    config = ClientConfig(enable_metadata_discovery=True, auto_register_client=False)

    async with AsyncClient(
        issuer=auth_server_url, auth=auth_strategy, config=config
    ) as oauth_client:
        endpoints = await oauth_client.get_endpoints()
        if not endpoints.authorize or not endpoints.token:
            raise ValueError(
                "Authorization server metadata is missing authorization_endpoint "
                "or token_endpoint"
            )
        return await oauth_client.exchange_authorization_code(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            client_id=client_id,
            resource=resource_url,
        )
