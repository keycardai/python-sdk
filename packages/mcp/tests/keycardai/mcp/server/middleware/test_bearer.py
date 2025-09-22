"""Tests for bearer authentication functions."""

from unittest.mock import Mock

from starlette.datastructures import URL, Headers
from starlette.requests import Request

from keycardai.mcp.server.middleware.bearer import (
    _get_bearer_token,
    _get_oauth_protected_resource_url,
)


class TestGetOAuthProtectedResourceUrl:
    """Tests for _get_oauth_protected_resource_url function."""

    def test_path_with_leading_slash(self):
        """Test with path that has leading slash."""
        request = Mock(spec=Request)
        request.url = Mock(spec=URL)
        request.url.path = "/api/resource"
        request.base_url = "https://example.com"

        result = _get_oauth_protected_resource_url(request)
        assert result == "https://example.com/.well-known/oauth-protected-resource/api/resource"

    def test_path_with_trailing_slash(self):
        """Test with path that has trailing slash."""
        request = Mock(spec=Request)
        request.url = Mock(spec=URL)
        request.url.path = "/api/resource/"
        request.base_url = "https://example.com"

        result = _get_oauth_protected_resource_url(request)
        assert result == "https://example.com/.well-known/oauth-protected-resource/api/resource"

    def test_base_url_with_trailing_slash(self):
        """Test with base URL that has trailing slash."""
        request = Mock(spec=Request)
        request.url = Mock(spec=URL)
        request.url.path = "/api/resource"
        request.base_url = "https://example.com/"

        result = _get_oauth_protected_resource_url(request)
        assert result == "https://example.com/.well-known/oauth-protected-resource/api/resource"

    def test_empty_path(self):
        """Test with empty path."""
        request = Mock(spec=Request)
        request.url = Mock(spec=URL)
        request.url.path = "/"
        request.base_url = "https://example.com"

        result = _get_oauth_protected_resource_url(request)
        assert result == "https://example.com/.well-known/oauth-protected-resource/"

    def test_root_path_only_slashes(self):
        """Test with path that is only slashes."""
        request = Mock(spec=Request)
        request.url = Mock(spec=URL)
        request.url.path = "///"
        request.base_url = "https://example.com"

        result = _get_oauth_protected_resource_url(request)
        assert result == "https://example.com/.well-known/oauth-protected-resource/"

    def test_nested_path(self):
        """Test with nested path."""
        request = Mock(spec=Request)
        request.url = Mock(spec=URL)
        request.url.path = "/api/v1/users/123/profile"
        request.base_url = "https://api.example.com"

        result = _get_oauth_protected_resource_url(request)
        assert result == "https://api.example.com/.well-known/oauth-protected-resource/api/v1/users/123/profile"

    def test_path_with_query_parameters_ignored(self):
        """Test that query parameters in path are handled correctly."""
        request = Mock(spec=Request)
        request.url = Mock(spec=URL)
        request.url.path = "/api/resource"
        request.url.query_params = {"id": "123"}
        request.base_url = "https://example.com"

        result = _get_oauth_protected_resource_url(request)
        assert result == "https://example.com/.well-known/oauth-protected-resource/api/resource"

    def test_https_with_port(self):
        """Test with HTTPS URL including port."""
        request = Mock(spec=Request)
        request.url = Mock(spec=URL)
        request.url.path = "/secure/resource"
        request.base_url = "https://secure.example.com:8443"

        result = _get_oauth_protected_resource_url(request)
        assert result == "https://secure.example.com:8443/.well-known/oauth-protected-resource/secure/resource"


class TestGetBearerToken:
    """Tests for _get_bearer_token function."""

    def test_valid_bearer_token(self):
        """Test with valid Bearer token."""
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": "Bearer abc123token"})

        result = _get_bearer_token(request)
        assert result == "abc123token"

    def test_valid_bearer_token_case_insensitive(self):
        """Test that Bearer is case insensitive."""
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": "bearer abc123token"})

        result = _get_bearer_token(request)
        assert result == "abc123token"

    def test_valid_bearer_token_mixed_case(self):
        """Test with mixed case Bearer."""
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": "BeArEr abc123token"})

        result = _get_bearer_token(request)
        assert result == "abc123token"

    def test_no_authorization_header(self):
        """Test with no Authorization header."""
        request = Mock(spec=Request)
        request.headers = Headers({})

        result = _get_bearer_token(request)
        assert result is None

    def test_empty_authorization_header(self):
        """Test with empty Authorization header."""
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": ""})

        result = _get_bearer_token(request)
        assert result is None

    def test_authorization_header_with_only_spaces(self):
        """Test with Authorization header containing only spaces."""
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": "   "})

        result = _get_bearer_token(request)
        assert result is None

    def test_basic_auth_instead_of_bearer(self):
        """Test with Basic auth instead of Bearer."""
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": "Basic dXNlcjpwYXNz"})

        result = _get_bearer_token(request)
        assert result is None

    def test_bearer_without_token(self):
        """Test with Bearer but no token."""
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": "Bearer"})

        result = _get_bearer_token(request)
        assert result is None

    def test_bearer_with_empty_token(self):
        """Test with Bearer followed by empty token."""
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": "Bearer "})

        result = _get_bearer_token(request)
        assert result == ""

    def test_bearer_with_multiple_spaces(self):
        """Test with Bearer and multiple spaces before token."""
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": "Bearer   token123"})

        # This should return None because split(" ") creates more than 2 parts
        result = _get_bearer_token(request)
        assert result is None

    def test_malformed_header_too_many_parts(self):
        """Test with malformed header having too many parts."""
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": "Bearer token123 extra"})

        result = _get_bearer_token(request)
        assert result is None

    def test_malformed_header_single_word(self):
        """Test with malformed header having only one word."""
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": "JustOneWord"})

        result = _get_bearer_token(request)
        assert result is None

    def test_bearer_with_complex_token(self):
        """Test with Bearer token containing special characters."""
        complex_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": f"Bearer {complex_token}"})

        result = _get_bearer_token(request)
        assert result == complex_token

    def test_bearer_with_numeric_token(self):
        """Test with Bearer token that is numeric."""
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": "Bearer 123456789"})

        result = _get_bearer_token(request)
        assert result == "123456789"

    def test_bearer_with_token_containing_equals(self):
        """Test with Bearer token containing equals signs (like base64)."""
        request = Mock(spec=Request)
        request.headers = Headers({"Authorization": "Bearer dGVzdA=="})

        result = _get_bearer_token(request)
        assert result == "dGVzdA=="

    def test_authorization_header_none_explicitly(self):
        """Test when headers.get returns None explicitly."""
        request = Mock(spec=Request)
        request.headers = Mock()
        request.headers.get.return_value = None

        result = _get_bearer_token(request)
        assert result is None

    def test_case_sensitivity_of_auth_scheme(self):
        """Test various case combinations of Bearer."""
        test_cases = [
            ("Bearer", "token123"),
            ("bearer", "token123"),
            ("BEARER", "token123"),
            ("BeArEr", "token123"),
            ("bEaReR", "token123"),
        ]

        for auth_scheme, expected_token in test_cases:
            request = Mock(spec=Request)
            request.headers = Headers({"Authorization": f"{auth_scheme} {expected_token}"})

            result = _get_bearer_token(request)
            assert result == expected_token, f"Failed for auth scheme: {auth_scheme}"


class TestGetBaseUrlMiddleware:
    """Tests for get_base_url function in middleware."""

    def _create_mock_request(self, base_url: str, path: str = "/", headers: dict[str, str] | None = None) -> Request:
        """Create a mock request with specified base URL, path, and headers."""
        if headers is None:
            headers = {}

        # Create a minimal ASGI scope for testing
        scope = {
            "type": "http",
            "method": "GET",
            "scheme": URL(base_url).scheme,
            "server": (URL(base_url).hostname, URL(base_url).port or (443 if URL(base_url).scheme == "https" else 80)),
            "path": path,
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
        return Request(scope)

    def test_proxy_aware_url_in_oauth_resource_url(self):
        """Test that _get_oauth_protected_resource_url uses proxy-aware base URL."""
        headers = {"x-forwarded-proto": "https"}
        request = self._create_mock_request("http://example.com", "/api/resource", headers)

        result = _get_oauth_protected_resource_url(request)
        assert result == "https://example.com/.well-known/oauth-protected-resource/api/resource"

    def test_no_proxy_headers_in_oauth_resource_url(self):
        """Test _get_oauth_protected_resource_url without proxy headers."""
        request = self._create_mock_request("http://example.com", "/api/resource")

        result = _get_oauth_protected_resource_url(request)
        assert result == "http://example.com/.well-known/oauth-protected-resource/api/resource"

    def test_aws_app_runner_scenario_in_middleware(self):
        """Test the AWS App Runner scenario in middleware context."""
        headers = {
            "host": "ppxrhd2bw4.us-east-1.awsapprunner.com",
            "x-forwarded-proto": "https",
            "x-forwarded-for": "92.238.31.228"
        }
        request = self._create_mock_request("http://ppxrhd2bw4.us-east-1.awsapprunner.com", "/zone123/api", headers)

        result = _get_oauth_protected_resource_url(request)
        assert result == "https://ppxrhd2bw4.us-east-1.awsapprunner.com/.well-known/oauth-protected-resource/zone123/api"
