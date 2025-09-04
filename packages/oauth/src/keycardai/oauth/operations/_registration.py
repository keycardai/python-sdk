import json

from ..exceptions import OAuthHttpError, OAuthProtocolError
from ..http import HTTPContext
from ..http._wire import HttpRequest, HttpResponse
from ..types.models import ClientRegistrationRequest, ClientRegistrationResponse
from ._exec import execute_async, execute_sync


def _validate_registration(req: ClientRegistrationRequest) -> None:
    if not req.client_name:
        raise ValueError("client_name required")
    # more validation…

def build_client_registration_http_request(
    req: ClientRegistrationRequest, endpoint: str, auth_headers: dict[str, str]
) -> HttpRequest:
    _validate_registration(req)
    payload = req.model_dump(
        mode="json",  # Automatically converts enums to their values
        exclude_none=True,  # Exclude None values
        exclude={"timeout"}  # Exclude timeout field (not part of OAuth spec)
    )

    body = json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json", **auth_headers}
    return HttpRequest(method="POST", url=endpoint, headers=headers, body=body)

def parse_client_registration_http_response(res: HttpResponse) -> ClientRegistrationResponse:
    # Handle HTTP error status codes first
    if res.status >= 400:
        response_body = res.body[:512].decode("utf-8", "ignore")
        raise OAuthHttpError(
            status_code=res.status,
            response_body=response_body,
            headers=dict(res.headers),
            operation="POST /register"
        )

    # Parse JSON response
    try:
        data = json.loads(res.body.decode("utf-8"))
    except Exception as e:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description="Invalid JSON in registration response",
            operation="POST /register"
        ) from e

    # Check for OAuth error response in successful HTTP status
    if isinstance(data, dict) and "error" in data:
        raise OAuthProtocolError(
            error=data["error"],
            error_description=data.get("error_description"),
            error_uri=data.get("error_uri"),
            operation="POST /register"
        )

    # Validate required fields before proceeding
    if not isinstance(data, dict) or "client_id" not in data:
        raise OAuthProtocolError(
            error="invalid_response",
            error_description="Missing required 'client_id' in registration response",
            operation="POST /register"
        )
    # Handle parameter normalization
    scope = data.get("scope")
    if isinstance(scope, str):
        scope = scope.split() if scope else None
    elif isinstance(scope, list):
        scope = scope if scope else None

    redirect_uris = data.get("redirect_uris")
    if isinstance(redirect_uris, str):
        redirect_uris = [redirect_uris]
    elif not isinstance(redirect_uris, list):
        redirect_uris = None

    grant_types = data.get("grant_types")
    if isinstance(grant_types, str):
        grant_types = [grant_types]
    elif not isinstance(grant_types, list):
        grant_types = None

    response_types = data.get("response_types")
    if isinstance(response_types, str):
        response_types = [response_types]
    elif not isinstance(response_types, list):
        response_types = None

    return ClientRegistrationResponse(
        client_id=data["client_id"],
        client_secret=data.get("client_secret"),
        client_id_issued_at=data.get("client_id_issued_at"),
        client_secret_expires_at=data.get("client_secret_expires_at"),
        client_name=data.get("client_name"),
        jwks_uri=data.get("jwks_uri"),
        jwks=data.get("jwks"),
        token_endpoint_auth_method=data.get("token_endpoint_auth_method"),
        redirect_uris=redirect_uris,
        grant_types=grant_types,
        response_types=response_types,
        scope=scope,
        registration_access_token=data.get("registration_access_token"),
        registration_client_uri=data.get("registration_client_uri"),
        client_uri=data.get("client_uri"),
        logo_uri=data.get("logo_uri"),
        tos_uri=data.get("tos_uri"),
        policy_uri=data.get("policy_uri"),
        software_id=data.get("software_id"),
        software_version=data.get("software_version"),
        raw=data,
        headers=dict(res.headers),
    )

def register_client(request: ClientRegistrationRequest, context: HTTPContext) -> ClientRegistrationResponse:
    headers = dict(context.auth.apply_headers())
    http_req = build_client_registration_http_request(request, context.endpoint, headers)
    http_res = execute_sync(context.transport, http_req, context.timeout)
    return parse_client_registration_http_response(http_res)

async def register_client_async(request: ClientRegistrationRequest, context: HTTPContext) -> ClientRegistrationResponse:
    headers = dict(context.auth.apply_headers())
    http_req = build_client_registration_http_request(request, context.endpoint, headers)
    http_res = await execute_async(context.transport, http_req, context.timeout)
    return parse_client_registration_http_response(http_res)
