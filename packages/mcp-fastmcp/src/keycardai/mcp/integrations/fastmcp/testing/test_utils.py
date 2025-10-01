from contextlib import contextmanager
from unittest.mock import Mock, patch

from keycardai.oauth.types.models import TokenResponse


@contextmanager
def mock_access_context(
    access_token: str = "test_access_token",
    resource_tokens: dict[str, str] | None = None,
    has_errors: bool = False,
    error_message: str = "Mock authentication error",
):
    """Mock the authentication system for testing.

    Args:
        access_token: Default access token to return for any resource (str)
        resource_tokens: Dict mapping resource URLs to specific access tokens (dict[str, str])
        has_errors: Whether the access context should report errors (bool)
        error_message: Error message to return when has_errors=True (str)

    Examples:
        # 1. Default - always returns access token
        with mock_access_context():
            # Will return "test_access_token" for any resource

        # 2. Returns access token for provided resource
        with mock_access_context(access_token="my_token"):
            # Will return "my_token" for any resource

        # 3. Return access token for provided dict of resources
        with mock_access_context(resource_tokens={
            "https://api.example.com": "token_123",
            "https://api.other.com": "token_456"
        }):
            # Will return specific tokens for each resource
            # Any resource not in the dict will set has_errors=True with "Resource not granted" message

        # 4. Returns error set to true and error message
        with mock_access_context(has_errors=True, error_message="Auth failed"):
            # Will report errors with the specified message
    """
    with patch('keycardai.mcp.integrations.fastmcp.provider.AccessContext') as mock_access_context_class, \
         patch('keycardai.mcp.integrations.fastmcp.provider.get_access_token') as mock_get_access_token:

        mock_access_context_instance = Mock()
        mock_access_context_instance.has_errors.return_value = has_errors

        if has_errors:
            # Return proper error structure matching AccessContext.get_errors()
            mock_access_context_instance.get_errors.return_value = {
                "resource_errors": {},
                "error": {"error": error_message}
            }
            mock_access_context_instance.access.side_effect = Exception(error_message)
        else:
            def mock_access_method(resource_url):
                if resource_tokens is not None:
                    if resource_url in resource_tokens:
                        # Return proper TokenResponse object
                        return TokenResponse(
                            access_token=resource_tokens[resource_url],
                            token_type="Bearer"
                        )
                    else:
                        # Resource not granted - set error state and raise exception
                        mock_access_context_instance.has_errors.return_value = True
                        mock_access_context_instance.get_errors.return_value = {
                            "resource_errors": {
                                resource_url: {"error": f"Resource not granted: {resource_url}"}
                            },
                            "error": None
                        }
                        from keycardai.mcp.server.exceptions import ResourceAccessError
                        raise ResourceAccessError()
                else:
                    # Return proper TokenResponse object
                    return TokenResponse(
                        access_token=access_token,
                        token_type="Bearer"
                    )

            mock_access_context_instance.access = mock_access_method
            mock_access_context_instance.get_errors.return_value = {
                "resource_errors": {},
                "error": None
            }

        mock_access_context_instance.set_bulk_tokens = Mock()
        mock_access_context_instance.set_error = Mock()
        mock_access_context_instance.set_resource_error = Mock()
        mock_access_context_class.return_value = mock_access_context_instance

        mock_user_token = Mock()
        mock_user_token.token = "user_jwt_token"
        mock_get_access_token.return_value = mock_user_token

        yield mock_access_context_instance
