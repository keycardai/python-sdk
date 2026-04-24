"""Token exchange orchestration for OAuth resource servers.

Provides framework-free token exchange logic that populates an AccessContext
with exchanged tokens for one or more target resources. This is the core
orchestration that both MCP's @grant() and Starlette's @protect() delegate to.
"""

from keycardai.oauth import AsyncClient
from keycardai.oauth.types.models import TokenExchangeRequest, TokenResponse

from .access_context import AccessContext
from .credentials import ApplicationCredential


async def exchange_tokens_for_resources(
    *,
    client: AsyncClient,
    resources: list[str],
    subject_token: str,
    access_context: AccessContext,
    application_credential: ApplicationCredential | None = None,
    auth_info: dict[str, str] | None = None,
    user_identifier: str | None = None,
) -> AccessContext:
    """Exchange a subject token for access tokens targeting one or more resources.

    For each resource, attempts token exchange via one of three paths:
    1. **Impersonation** — if ``user_identifier`` is provided, uses
       ``client.impersonate()`` for substitute-user exchange.
    2. **Application credential** — if ``application_credential`` is set,
       delegates request preparation to the credential provider.
    3. **Basic exchange** — standard RFC 8693 token exchange with no
       client authentication.

    Errors are stored per-resource on the AccessContext rather than raised,
    allowing partial-success scenarios.

    Args:
        client: Initialized OAuth async client for token exchange.
        resources: Target resource URLs to exchange tokens for.
        subject_token: The bearer token to exchange (from the authenticated user).
        access_context: Context object to populate with tokens/errors.
        application_credential: Optional credential provider for authenticated exchange.
        auth_info: Optional authentication context (zone_id, client_id, etc.).
        user_identifier: If set, use impersonation (substitute-user) exchange.

    Returns:
        The same AccessContext, populated with tokens and/or per-resource errors.
    """
    access_tokens: dict[str, TokenResponse] = {}

    for resource in resources:
        try:
            if user_identifier is not None:
                token_response = await client.impersonate(
                    user_identifier=user_identifier,
                    resource=resource,
                )
            elif application_credential:
                token_exchange_request = (
                    await application_credential.prepare_token_exchange_request(
                        client=client,
                        subject_token=subject_token,
                        resource=resource,
                        auth_info=auth_info,
                    )
                )
                token_response = await client.exchange_token(token_exchange_request)
            else:
                token_exchange_request = TokenExchangeRequest(
                    subject_token=subject_token,
                    resource=resource,
                    subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                )
                token_response = await client.exchange_token(token_exchange_request)

            access_tokens[resource] = token_response
        except Exception as e:
            error_dict: dict[str, str] = {
                "message": f"Token exchange failed for {resource}",
            }
            if hasattr(e, "error"):
                error_dict["code"] = e.error
            if hasattr(e, "error_description") and e.error_description:
                error_dict["description"] = e.error_description
            if not hasattr(e, "error"):
                error_dict["raw_error"] = str(e)

            access_context.set_resource_error(resource, error_dict)

    access_context.set_bulk_tokens(access_tokens)
    return access_context
