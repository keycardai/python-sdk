"""Common OAuth exceptions for OAuth 2.0 operations.

This module provides standardized exception classes for various OAuth operations
as defined across multiple OAuth 2.0 RFCs.

References:
- RFC 6749: The OAuth 2.0 Authorization Framework
- RFC 8693: OAuth 2.0 Token Exchange
- RFC 7662: OAuth 2.0 Token Introspection
- RFC 7009: OAuth 2.0 Token Revocation
"""



class OAuthError(Exception):
    """Base class for all STS-related errors.

    Provides a foundation for all Security Token Service exceptions
    with optional error chaining support.
    """

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


class OAuthInvalidTokenError(OAuthError):
    """Error for invalid or malformed tokens.

    Raised when:
    - Token is empty or not a string
    - Token format is invalid
    - Token signature verification fails

    Related RFC: RFC 6749 Section 5.2 (invalid_grant error)
    """

    code = "invalid_token"


class OAuthTokenExpiredError(OAuthError):
    """Error for expired tokens.

    Raised when a token has exceeded its lifetime.

    Related RFC: RFC 6749 Section 5.2, RFC 7662 Section 2.2
    """

    code = "token_expired"


class OAuthTokenInactiveError(OAuthError):
    """Error for inactive tokens.

    Raised when token introspection indicates the token is not active.

    Related RFC: RFC 7662 Section 2.2 (active: false)
    """

    code = "token_inactive"


class OAuthRequestError(OAuthError):
    """Error for failed HTTP requests to STS endpoints.

    Covers HTTP-level failures including network issues and server errors.
    """

    code = "request_failed"

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_text: str | None = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class OAuthUnsupportedGrantTypeError(OAuthError):
    """Error for unsupported grant types.

    Related RFC: RFC 6749 Section 5.2 (unsupported_grant_type)
    """

    code = "unsupported_grant_type"


class OAuthInvalidScopeError(OAuthError):
    """Error for invalid or unauthorized scopes.

    Related RFC: RFC 6749 Section 5.2 (invalid_scope)
    """

    code = "invalid_scope"


class OAuthInvalidClientError(OAuthError):
    """Error for invalid client credentials or authentication.

    Related RFC: RFC 6749 Section 5.2 (invalid_client)
    """

    code = "invalid_client"
