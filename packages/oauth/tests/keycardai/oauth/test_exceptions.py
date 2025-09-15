"""Tests for OAuth 2.0 exception hierarchy."""


from keycardai.oauth.exceptions import (
    AuthenticationError,
    ConfigError,
    NetworkError,
    OAuthError,
    OAuthHttpError,
    OAuthProtocolError,
    TokenExchangeError,
)


class TestOAuthError:
    """Test base OAuthError class."""

    def test_create_basic(self):
        """Test creating basic OAuthError."""
        error = OAuthError("Test error")

        assert str(error) == "Test error"
        assert error.cause is None

    def test_create_with_cause(self):
        """Test creating OAuthError with cause."""
        cause = ValueError("Original error")
        error = OAuthError("Test error", cause=cause)

        assert str(error) == "Test error"
        assert error.cause is cause


class TestOAuthHttpError:
    """Test OAuthHttpError class."""

    def test_create_minimal(self):
        """Test creating OAuthHttpError with minimal fields."""
        error = OAuthHttpError(
            status_code=500,
            response_body="Internal Server Error",
            headers={},
            operation="GET /token"
        )

        assert error.status_code == 500
        assert error.response_body == "Internal Server Error"
        assert error.headers == {}
        assert error.operation == "GET /token"

    def test_create_with_all_fields(self):
        """Test creating OAuthHttpError with all fields."""
        headers = {"Content-Type": "application/json"}
        error = OAuthHttpError(
            status_code=400,
            response_body='{"error": "invalid_client"}',
            headers=headers,
            operation="POST /token"
        )

        assert error.status_code == 400
        assert error.response_body == '{"error": "invalid_client"}'
        assert error.headers == headers
        assert error.operation == "POST /token"
        assert error.retriable is False  # 400 errors are not retriable


class TestOAuthProtocolError:
    """Test OAuthProtocolError class."""

    def test_create_minimal(self):
        """Test creating OAuthProtocolError with minimal fields."""
        error = OAuthProtocolError(
            error="invalid_client",
            operation="POST /token"
        )

        assert error.error == "invalid_client"
        assert error.error_description is None
        assert error.error_uri is None
        assert error.operation == "POST /token"

    def test_create_with_all_fields(self):
        """Test creating OAuthProtocolError with all fields."""
        error = OAuthProtocolError(
            error="invalid_grant",
            error_description="The provided authorization grant is invalid",
            error_uri="https://example.com/error/invalid_grant",
            operation="POST /token"
        )

        assert error.error == "invalid_grant"
        assert error.error_description == "The provided authorization grant is invalid"
        assert error.error_uri == "https://example.com/error/invalid_grant"
        assert error.operation == "POST /token"


class TestNetworkError:
    """Test NetworkError class."""

    def test_create_minimal(self):
        """Test creating NetworkError with minimal fields."""
        cause = ConnectionError("Connection refused")
        error = NetworkError(
            cause=cause,
            operation="POST /token",
            retriable=True
        )

        assert error.operation == "POST /token"
        assert error.retriable is True
        assert error.cause is cause

    def test_create_with_cause(self):
        """Test creating NetworkError with cause."""
        cause = ConnectionError("Connection refused")
        error = NetworkError(
            cause=cause,
            operation="POST /token",
            retriable=True
        )

        assert error.operation == "POST /token"
        assert error.retriable is True
        assert error.cause is cause


class TestConfigError:
    """Test ConfigError class."""

    def test_create(self):
        """Test creating ConfigError."""
        error = ConfigError("Invalid configuration: missing client_id")

        assert str(error) == "Invalid configuration: missing client_id"
        assert isinstance(error, OAuthError)





class TestExceptionHierarchy:
    """Test exception inheritance and hierarchy."""

    def test_all_inherit_from_oauth_error(self):
        """Test that all OAuth exceptions inherit from OAuthError."""
        exceptions = [
            OAuthHttpError(400, "Bad Request", {}, "GET /test"),
            OAuthProtocolError("invalid_client", operation="POST /token"),
            NetworkError(ConnectionError("Test"), "GET /test", retriable=True),
            ConfigError("Test config error"),
            AuthenticationError("Test auth error"),
            TokenExchangeError("invalid_target", operation="POST /token"),
        ]

        for exc in exceptions:
            assert isinstance(exc, OAuthError)

    def test_protocol_errors_not_retriable(self):
        """Test that protocol errors are not retriable by default."""
        protocol_errors = [
            OAuthProtocolError("invalid_client", operation="POST /token"),
            TokenExchangeError("invalid_target", operation="POST /token"),
        ]

        for error in protocol_errors:
            # Protocol errors should not be retriable by default
            assert not getattr(error, "retriable", True)

    def test_exception_messages(self):
        """Test that exceptions have meaningful string representations."""
        errors = {
            OAuthError("Test message"): "Test message",
            ConfigError("Config issue"): "Config issue",
            AuthenticationError("Auth failed"): "Auth failed",
        }

        for error, expected_message in errors.items():
            assert str(error) == expected_message


class TestExceptionIntegration:
    """Test exception integration and usage patterns."""

    def test_all_exceptions_importable(self):
        """Test that all exceptions can be imported together."""
        from keycardai.oauth.exceptions import (
            OAuthError,
            OAuthHttpError,
            OAuthProtocolError,
        )

        # Should be able to create instances
        base_error = OAuthError("Base error")
        http_error = OAuthHttpError(500, "Server Error", {}, "GET /test")
        protocol_error = OAuthProtocolError("invalid_client", operation="POST /token")

        assert isinstance(base_error, Exception)
        assert isinstance(http_error, OAuthError)
        assert isinstance(protocol_error, OAuthError)

    def test_exception_chaining(self):
        """Test that exceptions support proper cause chaining."""
        original_error = ValueError("Original problem")
        oauth_error = OAuthError("OAuth error occurred", cause=original_error)

        assert oauth_error.cause is original_error
        assert str(oauth_error) == "OAuth error occurred"

    def test_dataclass_functionality(self):
        """Test that dataclass exceptions work properly."""
        error1 = OAuthHttpError(400, "Bad Request", {}, "GET /test")
        error2 = OAuthHttpError(400, "Bad Request", {}, "GET /test")

        # Should have proper dataclass behavior
        assert error1.status_code == error2.status_code
        assert error1.response_body == error2.response_body
        assert error1.operation == error2.operation
