"""Deprecated re-export of :mod:`keycardai.fastmcp.provider`.

The implementation moved to ``keycardai.fastmcp.provider``. This module is
preserved so existing callers using
``from keycardai.mcp.integrations.fastmcp.provider import ...`` continue to
work; new code should import from ``keycardai.fastmcp.provider`` directly.
"""

from keycardai.fastmcp.provider import (  # noqa: F401
    AccessContext,
    AnyHttpUrl,
    ApplicationCredential,
    AsyncClient,
    AuthProvider,
    Client,
    ClientFactory,
    ClientSecret,
    Context,
    DefaultClientFactory,
    EKSWorkloadIdentity,
    JWTVerifier,
    MissingContextError,
    NoneAuth,
    RemoteAuthProvider,
    ResourceAccessError,
    TokenExchangeRequest,
    TokenResponse,
    WebIdentity,
    extract_scopes,
    get_access_token,
    get_claims,
)
