"""Unit tests for the OpenID Connect UserInfo operation (OIDC Core 1.0 Section 5.3)."""

from unittest.mock import AsyncMock, Mock

import pytest

from keycardai.oauth.exceptions import (
    ConfigError,
    InvalidTokenError,
    OAuthHttpError,
    OAuthProtocolError,
)
from keycardai.oauth.http._context import build_http_context
from keycardai.oauth.http._wire import HttpResponse
from keycardai.oauth.operations._userinfo import (
    build_userinfo_http_request,
    fetch_userinfo,
    fetch_userinfo_async,
    parse_userinfo_http_response,
    resolve_userinfo_endpoint,
)
from keycardai.oauth.types.models import (
    AuthorizationServerMetadata,
    UserInfoRequest,
    UserInfoResponse,
)

CLAIMS_BODY = (
    b'{"sub": "user-123", "email": "kim@example.com", "name": "Kim", '
    b'"groups": ["engineering"], "custom_claim": {"tier": "gold"}}'
)


def _context(transport):
    return build_http_context(
        endpoint="https://auth.example.com/userinfo",
        transport=transport,
        auth=Mock(apply_headers=Mock(return_value={"Authorization": "Basic client"})),
        user_agent="TestClient/1.0",
        timeout=30.0,
    )


class TestUserInfoRequestBuilding:
    """Request construction (GET with a Bearer credential)."""

    def test_build_userinfo_http_request(self):
        http_req = build_userinfo_http_request(
            UserInfoRequest(access_token="user-access-token"), _context(Mock())
        )

        assert http_req.method == "GET"
        assert http_req.url == "https://auth.example.com/userinfo"
        assert http_req.headers["Authorization"] == "Bearer user-access-token"
        assert http_req.headers["Accept"] == "application/json"
        assert http_req.headers["User-Agent"] == "TestClient/1.0"
        assert http_req.body is None

    def test_build_userinfo_http_request_ignores_client_auth_strategy(self):
        """UserInfo authenticates the user, so the client's own auth is not applied."""
        context = _context(Mock())

        http_req = build_userinfo_http_request(
            UserInfoRequest(access_token="user-access-token"), context
        )

        assert http_req.headers["Authorization"] == "Bearer user-access-token"
        context.auth.apply_headers.assert_not_called()


class TestUserInfoEndpointResolution:
    """Endpoint resolution from discovered metadata."""

    def test_resolve_userinfo_endpoint(self):
        metadata = AuthorizationServerMetadata(
            issuer="https://auth.example.com",
            userinfo_endpoint="https://auth.example.com/userinfo",
        )

        assert resolve_userinfo_endpoint(metadata) == "https://auth.example.com/userinfo"

    def test_resolve_userinfo_endpoint_missing_is_a_config_error(self):
        metadata = AuthorizationServerMetadata(issuer="https://auth.example.com")

        with pytest.raises(ConfigError, match="userinfo_endpoint"):
            resolve_userinfo_endpoint(metadata)


class TestUserInfoResponseParsing:
    """Response parsing per the spec's unit test table."""

    def test_claims_are_returned_unfiltered(self):
        result = parse_userinfo_http_response(
            HttpResponse(
                status=200,
                headers={"Content-Type": "application/json"},
                body=CLAIMS_BODY,
            )
        )

        assert isinstance(result, UserInfoResponse)
        assert result.sub == "user-123"
        assert result.claims["email"] == "kim@example.com"
        assert result.claims["groups"] == ["engineering"]
        assert result.claims["custom_claim"] == {"tier": "gold"}
        assert result.claims["sub"] == "user-123"

    def test_missing_sub_is_a_protocol_error(self):
        with pytest.raises(OAuthProtocolError, match="sub"):
            parse_userinfo_http_response(
                HttpResponse(
                    status=200,
                    headers={"Content-Type": "application/json"},
                    body=b'{"email": "kim@example.com"}',
                )
            )

    def test_invalid_token_challenge_is_an_authorization_error(self):
        with pytest.raises(InvalidTokenError, match="invalid_token") as exc_info:
            parse_userinfo_http_response(
                HttpResponse(
                    status=401,
                    headers={
                        "WWW-Authenticate": 'Bearer error="invalid_token", '
                        'error_description="The access token expired"'
                    },
                    body=b"",
                )
            )

        assert exc_info.value.error_code == "invalid_token"

    def test_401_without_challenge_is_still_an_invalid_token_error(self):
        with pytest.raises(InvalidTokenError) as exc_info:
            parse_userinfo_http_response(
                HttpResponse(status=401, headers={}, body=b"")
            )

        assert exc_info.value.error_code == "invalid_token"

    def test_challenge_error_code_is_carried_on_the_exception(self):
        """A challenge naming another RFC 6750 code is reported, not flattened."""
        with pytest.raises(InvalidTokenError) as exc_info:
            parse_userinfo_http_response(
                HttpResponse(
                    status=401,
                    headers={
                        "WWW-Authenticate": 'Bearer error="insufficient_scope", '
                        'scope="openid profile"'
                    },
                    body=b"",
                )
            )

        assert exc_info.value.error_code == "insufficient_scope"

    def test_signed_response_is_a_protocol_error(self):
        with pytest.raises(OAuthProtocolError, match="application/jwt"):
            parse_userinfo_http_response(
                HttpResponse(
                    status=200,
                    headers={"Content-Type": "application/jwt"},
                    body=b"eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyLTEyMyJ9.sig",
                )
            )

    def test_invalid_json_is_a_protocol_error(self):
        with pytest.raises(OAuthProtocolError, match="Invalid JSON"):
            parse_userinfo_http_response(
                HttpResponse(
                    status=200,
                    headers={"Content-Type": "application/json"},
                    body=b"not json {",
                )
            )

    def test_non_object_body_is_a_protocol_error(self):
        with pytest.raises(OAuthProtocolError, match="JSON object"):
            parse_userinfo_http_response(
                HttpResponse(
                    status=200,
                    headers={"Content-Type": "application/json"},
                    body=b'["user-123"]',
                )
            )

    def test_other_non_2xx_is_an_http_error(self):
        with pytest.raises(OAuthHttpError, match="HTTP 500"):
            parse_userinfo_http_response(
                HttpResponse(status=500, headers={}, body=b"boom")
            )


class TestUserInfoOperation:
    """End-to-end operation behavior over a mocked transport."""

    def test_fetch_userinfo_sync(self):
        transport = Mock()
        transport.request_raw.return_value = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=CLAIMS_BODY,
        )

        result = fetch_userinfo(
            UserInfoRequest(access_token="user-access-token"), _context(transport)
        )

        assert result.sub == "user-123"
        sent = transport.request_raw.call_args[0][0]
        assert sent.method == "GET"
        assert sent.headers["Authorization"] == "Bearer user-access-token"

    @pytest.mark.asyncio
    async def test_fetch_userinfo_async(self):
        transport = AsyncMock()
        transport.request_raw.return_value = HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=CLAIMS_BODY,
        )

        result = await fetch_userinfo_async(
            UserInfoRequest(access_token="user-access-token"), _context(transport)
        )

        assert result.sub == "user-123"
        assert result.claims["email"] == "kim@example.com"

    def test_empty_access_token_is_rejected(self):
        with pytest.raises(ValueError):
            UserInfoRequest(access_token="")
