"""Unit tests for metadata handler functions.

These tests focus on URL handling and slash character edge cases.
"""

import pytest
from pydantic import AnyHttpUrl
from starlette.datastructures import URL
from starlette.requests import Request

from keycardai.mcp.server.handlers.metadata import (
    _create_resource_url,
    _create_zone_scoped_authorization_server_url,
    _get_zone_id_from_path,
    _is_authorization_server_zone_scoped,
    _remove_well_known_prefix,
    _strip_zone_id_from_path,
)
from keycardai.mcp.server.shared.starlette import get_base_url


class TestIsAuthorizationServerZoneScoped:
    """Test _is_authorization_server_zone_scoped function."""

    def test_zone_scoped_url(self):
        """Test with production zone-scoped URL (zone-id.keycard.cloud)."""
        urls = [AnyHttpUrl("https://zone123.keycard.cloud")]
        assert _is_authorization_server_zone_scoped(urls) is True

    def test_non_zone_scoped_url(self):
        """Test with production non-zone-scoped URL (keycard.cloud)."""
        urls = [AnyHttpUrl("https://keycard.cloud")]
        assert _is_authorization_server_zone_scoped(urls) is False


    def test_multiple_urls(self):
        """Test with multiple URLs (should return False)."""
        urls = [
            AnyHttpUrl("https://zone123.keycard.ai"),
            AnyHttpUrl("https://zone456.keycard.ai")
        ]
        assert _is_authorization_server_zone_scoped(urls) is False

    def test_empty_list(self):
        """Test with empty URL list."""
        urls = []
        assert _is_authorization_server_zone_scoped(urls) is False

    def test_with_port(self):
        """Test with localhost and port."""
        urls = [AnyHttpUrl("http://localhost:8000")]
        assert _is_authorization_server_zone_scoped(urls) is False

    def test_ip_address(self):
        """Test with IP address."""
        urls = [AnyHttpUrl("https://192.168.1.1")]
        assert _is_authorization_server_zone_scoped(urls) is False


class TestGetZoneIdFromPath:
    """Test _get_zone_id_from_path function."""

    def test_simple_zone_id(self):
        """Test extracting zone ID from simple path."""
        assert _get_zone_id_from_path("zone123/api/v1") == "zone123"

    def test_zone_id_with_leading_slash(self):
        """Test with leading slash."""
        assert _get_zone_id_from_path("/zone123/api/v1") == "zone123"

    def test_zone_id_with_trailing_slash(self):
        """Test with trailing slash."""
        assert _get_zone_id_from_path("zone123/api/v1/") == "zone123"

    def test_zone_id_with_both_slashes(self):
        """Test with both leading and trailing slashes."""
        assert _get_zone_id_from_path("/zone123/api/v1/") == "zone123"

    def test_zone_id_only(self):
        """Test with zone ID only."""
        assert _get_zone_id_from_path("zone123") == "zone123"

    def test_zone_id_only_with_slashes(self):
        """Test with zone ID only and slashes."""
        assert _get_zone_id_from_path("/zone123/") == "zone123"

    def test_empty_path(self):
        """Test with empty path."""
        assert _get_zone_id_from_path("") is None

    def test_root_path(self):
        """Test with root path."""
        assert _get_zone_id_from_path("/") is None

    def test_only_slashes(self):
        """Test with only slashes."""
        assert _get_zone_id_from_path("///") is None

    def test_path_with_special_characters(self):
        """Test with special characters in zone ID."""
        assert _get_zone_id_from_path("zone-123_test/api") == "zone-123_test"

    def test_path_with_dots(self):
        """Test with dots in zone ID."""
        assert _get_zone_id_from_path("zone.123/api") == "zone.123"


class TestRemoveWellKnownPrefix:
    """Test _remove_well_known_prefix function."""

    def test_path_with_prefix(self):
        """Test removing well-known prefix."""
        path = ".well-known/oauth-protected-resource/zone123/api"
        expected = "/zone123/api"
        assert _remove_well_known_prefix(path) == expected

    def test_path_with_prefix_and_leading_slash(self):
        """Test with leading slash."""
        path = "/.well-known/oauth-protected-resource/zone123/api"
        expected = "/zone123/api"
        assert _remove_well_known_prefix(path) == expected

    def test_path_with_prefix_and_trailing_slash(self):
        """Test with trailing slash."""
        path = ".well-known/oauth-protected-resource/zone123/api/"
        expected = "/zone123/api"
        assert _remove_well_known_prefix(path) == expected

    def test_path_with_prefix_and_both_slashes(self):
        """Test with both leading and trailing slashes."""
        path = "/.well-known/oauth-protected-resource/zone123/api/"
        expected = "/zone123/api"
        assert _remove_well_known_prefix(path) == expected

    def test_path_without_prefix(self):
        """Test path without well-known prefix."""
        path = "/zone123/api/v1"
        assert _remove_well_known_prefix(path) == "zone123/api/v1"

    def test_empty_path(self):
        """Test with empty path."""
        assert _remove_well_known_prefix("") == ""

    def test_root_path(self):
        """Test with root path."""
        assert _remove_well_known_prefix("/") == ""

    def test_prefix_only(self):
        """Test with prefix only."""
        path = ".well-known/oauth-protected-resource"
        assert _remove_well_known_prefix(path) == ""

    def test_prefix_only_with_slashes(self):
        """Test with prefix only and slashes."""
        path = "/.well-known/oauth-protected-resource/"
        assert _remove_well_known_prefix(path) == ""

    def test_partial_prefix_match(self):
        """Test with partial prefix match (should not remove)."""
        path = ".well-known/oauth-protected"
        assert _remove_well_known_prefix(path) == ".well-known/oauth-protected"


class TestCreateZoneScopedAuthorizationServerUrl:
    """Test _create_zone_scoped_authorization_server_url function."""

    def test_https_url(self):
        """Test with production HTTPS URL."""
        zone_id = "zone123"
        auth_server = AnyHttpUrl("https://keycard.cloud")
        result = _create_zone_scoped_authorization_server_url(zone_id, auth_server)
        assert str(result) == "https://zone123.keycard.cloud/"


    def test_url_with_port(self):
        """Test with production URL containing port."""
        zone_id = "zone123"
        auth_server = AnyHttpUrl("https://keycard.cloud:8443")
        result = _create_zone_scoped_authorization_server_url(zone_id, auth_server)
        assert str(result) == "https://zone123.keycard.cloud:8443/"

    def test_url_with_path(self):
        """Test with URL containing path (should be ignored)."""
        zone_id = "zone123"
        auth_server = AnyHttpUrl("https://keycard.cloud/oauth")
        result = _create_zone_scoped_authorization_server_url(zone_id, auth_server)
        assert str(result) == "https://zone123.keycard.cloud/"

    def test_url_with_trailing_slash(self):
        """Test with URL having trailing slash."""
        zone_id = "zone123"
        auth_server = AnyHttpUrl("https://keycard.cloud/")
        result = _create_zone_scoped_authorization_server_url(zone_id, auth_server)
        assert str(result) == "https://zone123.keycard.cloud/"

    def test_zone_id_with_special_characters(self):
        """Test with zone ID containing special characters."""
        zone_id = "zone-123_test"
        auth_server = AnyHttpUrl("https://keycard.cloud")
        result = _create_zone_scoped_authorization_server_url(zone_id, auth_server)
        assert str(result) == "https://zone-123_test.keycard.cloud/"

class TestStripZoneIdFromPath:
    """Test _strip_zone_id_from_path function."""

    def test_strip_zone_id(self):
        """Test stripping zone ID from path."""
        zone_id = "zone123"
        path = "zone123/api/v1"
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == "/api/v1"

    def test_strip_zone_id_with_leading_slash(self):
        """Test with leading slash in path."""
        zone_id = "zone123"
        path = "/zone123/api/v1"
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == "/api/v1"

    def test_strip_zone_id_with_trailing_slash(self):
        """Test with trailing slash in path."""
        zone_id = "zone123"
        path = "zone123/api/v1/"
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == "/api/v1"

    def test_strip_zone_id_with_both_slashes(self):
        """Test with both leading and trailing slashes."""
        zone_id = "zone123"
        path = "/zone123/api/v1/"
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == "/api/v1"

    def test_zone_id_only(self):
        """Test with zone ID only in path."""
        zone_id = "zone123"
        path = "zone123"
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == ""

    def test_zone_id_only_with_slashes(self):
        """Test with zone ID only and slashes."""
        zone_id = "zone123"
        path = "/zone123/"
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == ""

    def test_path_not_starting_with_zone_id(self):
        """Test with path not starting with zone ID."""
        zone_id = "zone123"
        path = "api/zone123/v1"
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == "api/zone123/v1"

    def test_partial_zone_id_match(self):
        """Test with partial zone ID match."""
        zone_id = "zone123"
        path = "zone12/api/v1"
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == "zone12/api/v1"

    def test_zone_id_as_substring(self):
        """Test with zone ID as substring of first segment."""
        zone_id = "zone"
        path = "zone123/api/v1"
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == "123/api/v1"

    def test_empty_path(self):
        """Test with empty path."""
        zone_id = "zone123"
        path = ""
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == ""

    def test_empty_zone_id(self):
        """Test with empty zone ID."""
        zone_id = ""
        path = "zone123/api/v1"
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == "zone123/api/v1"


class TestCreateResourceUrl:
    """Test _create_resource_url function."""

    def test_simple_url_creation(self):
        """Test creating simple resource URL."""
        base_url = "https://api.example.com"
        path = "/users/123"
        result = _create_resource_url(base_url, path)
        assert str(result) == "https://api.example.com/users/123"

    def test_base_url_with_trailing_slash(self):
        """Test with base URL having trailing slash."""
        base_url = "https://api.example.com/"
        path = "/users/123"
        result = _create_resource_url(base_url, path)
        assert str(result) == "https://api.example.com/users/123"

    def test_path_without_leading_slash(self):
        """Test with path without leading slash."""
        base_url = "https://api.example.com"
        path = "users/123"
        result = _create_resource_url(base_url, path)
        assert str(result) == "https://api.example.com/users/123"

    def test_both_with_slashes(self):
        """Test with both base URL and path having slashes."""
        base_url = "https://api.example.com/"
        path = "/users/123"
        result = _create_resource_url(base_url, path)
        assert str(result) == "https://api.example.com/users/123"

    def test_empty_path(self):
        """Test with empty path."""
        base_url = "https://api.example.com"
        path = ""
        result = _create_resource_url(base_url, path)
        assert str(result) == "https://api.example.com/"

    def test_root_path(self):
        """Test with root path."""
        base_url = "https://api.example.com"
        path = "/"
        result = _create_resource_url(base_url, path)
        assert str(result) == "https://api.example.com/"

    def test_path_with_trailing_slash(self):
        """Test with path having trailing slash (should be stripped)."""
        base_url = "https://api.example.com"
        path = "/users/123/"
        result = _create_resource_url(base_url, path)
        assert str(result) == "https://api.example.com/users/123"

    def test_complex_path(self):
        """Test with complex path."""
        base_url = "https://api.example.com"
        path = "/v1/users/123/profile"
        result = _create_resource_url(base_url, path)
        assert str(result) == "https://api.example.com/v1/users/123/profile"

    def test_base_url_with_port(self):
        """Test with base URL containing port."""
        base_url = "https://api.example.com:8443"
        path = "/users/123"
        result = _create_resource_url(base_url, path)
        assert str(result) == "https://api.example.com:8443/users/123"

    def test_base_url_with_path(self):
        """Test with base URL already containing path."""
        base_url = "https://api.example.com/v1"
        path = "/users/123"
        result = _create_resource_url(base_url, path)
        assert str(result) == "https://api.example.com/v1/users/123"

class TestEdgeCases:
    """Test edge cases and combinations."""

    def test_multiple_consecutive_slashes(self):
        """Test handling of multiple consecutive slashes."""
        path = "///.well-known/oauth-protected-resource///zone123///api///"
        result = _remove_well_known_prefix(path)
        assert result == "///zone123///api"

        # Test _get_zone_id_from_path
        zone_id = _get_zone_id_from_path("///zone123///api///")
        assert zone_id == "zone123"

        # Test _strip_zone_id_from_path
        stripped = _strip_zone_id_from_path("zone123", "///zone123///api///")
        assert stripped == "///api"

    def test_url_encoding_characters(self):
        """Test with URL-encoded characters."""
        zone_id = "zone%20123"
        path = "zone%20123/api/v1"
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == "/api/v1"

    def test_unicode_characters(self):
        """Test with Unicode characters."""
        zone_id = "zone测试"
        path = "zone测试/api/v1"
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == "/api/v1"

    def test_very_long_paths(self):
        """Test with very long paths."""
        zone_id = "zone123"
        long_path = "/".join([f"segment{i}" for i in range(100)])
        path = f"zone123/{long_path}"
        result = _strip_zone_id_from_path(zone_id, path)
        assert result == f"/{long_path}"

    def test_case_sensitivity(self):
        """Test case sensitivity."""
        zone_id = "Zone123"
        path = "zone123/api/v1"
        result = _strip_zone_id_from_path(zone_id, path)
        # Should not match due to case difference
        assert result == "zone123/api/v1"


class TestGetBaseUrl:
    """Test get_base_url function."""

    def _create_mock_request(self, base_url: str, headers: dict[str, str] | None = None) -> Request:
        """Create a mock request with specified base URL and headers."""
        if headers is None:
            headers = {}

        parsed_url = URL(base_url)
        # Create a minimal ASGI scope for testing
        scope = {
            "type": "http",
            "method": "GET",
            "scheme": parsed_url.scheme,
            "server": (parsed_url.hostname, parsed_url.port or (443 if parsed_url.scheme == "https" else 80)),
            "path": parsed_url.path or "/",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
        return Request(scope)

    def test_no_proxy_headers(self):
        """Test with no proxy headers - should return original base URL."""
        request = self._create_mock_request("http://example.com")
        result = get_base_url(request)
        assert result == "http://example.com"

    def test_with_x_forwarded_proto_https(self):
        """Test with X-Forwarded-Proto header indicating HTTPS."""
        headers = {"x-forwarded-proto": "https"}
        request = self._create_mock_request("http://example.com", headers)
        result = get_base_url(request)
        assert result == "https://example.com"

    def test_with_x_forwarded_proto_http(self):
        """Test with X-Forwarded-Proto header indicating HTTP."""
        headers = {"x-forwarded-proto": "http"}
        request = self._create_mock_request("https://example.com", headers)
        result = get_base_url(request)
        assert result == "http://example.com"

    def test_with_port_number(self):
        """Test with port number in base URL."""
        headers = {"x-forwarded-proto": "https"}
        request = self._create_mock_request("http://example.com:8080", headers)
        result = get_base_url(request)
        assert result == "https://example.com:8080"

    def test_with_path_in_base_url(self):
        """Test with path in base URL - path should be ignored for base URL."""
        headers = {"x-forwarded-proto": "https"}
        request = self._create_mock_request("http://example.com/api/v1", headers)
        result = get_base_url(request)
        assert result == "https://example.com"

    def test_case_insensitive_header(self):
        """Test that header matching is case insensitive (Starlette handles this)."""
        headers = {"X-Forwarded-Proto": "https"}
        request = self._create_mock_request("http://example.com", headers)
        result = get_base_url(request)
        assert result == "https://example.com"

    def test_trailing_slash_handling(self):
        """Test that trailing slashes are properly handled."""
        headers = {"x-forwarded-proto": "https"}
        request = self._create_mock_request("http://example.com/", headers)
        result = get_base_url(request)
        assert result == "https://example.com"

    def test_aws_app_runner_scenario(self):
        """Test the specific AWS App Runner scenario from the issue."""
        headers = {
            "host": "ppxrhd2bw4.us-east-1.awsapprunner.com",
            "x-forwarded-proto": "https",
            "x-forwarded-for": "92.238.31.228"
        }
        request = self._create_mock_request("http://ppxrhd2bw4.us-east-1.awsapprunner.com", headers)
        result = get_base_url(request)
        assert result == "https://ppxrhd2bw4.us-east-1.awsapprunner.com"


if __name__ == "__main__":
    pytest.main([__file__])
