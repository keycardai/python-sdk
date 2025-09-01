"""Tests for bearer token utility functions."""


from keycardai.oauth.utils import (
    create_auth_header,
    extract_bearer_token,
    validate_bearer_format,
)


class TestBearerTokenUtils:
    """Test bearer token utility functions."""

    def test_extract_bearer_token_valid(self):
        """Test extracting bearer token from valid Authorization header."""
        # Standard case
        token = extract_bearer_token("Bearer abc123xyz")
        assert token == "abc123xyz"

        # Case insensitive
        token = extract_bearer_token("bearer abc123xyz")
        assert token == "abc123xyz"

        # With mixed case
        token = extract_bearer_token("Bearer ABC123xyz")
        assert token == "ABC123xyz"

    def test_extract_bearer_token_invalid(self):
        """Test extracting bearer token from invalid headers."""
        # None header
        token = extract_bearer_token(None)
        assert token is None

        # Empty string
        token = extract_bearer_token("")
        assert token is None

        # Wrong scheme
        token = extract_bearer_token("Basic abc123")
        assert token is None

        # Missing token
        token = extract_bearer_token("Bearer")
        assert token is None

        # Too many parts
        token = extract_bearer_token("Bearer abc123 extra")
        assert token is None

        # Wrong format
        token = extract_bearer_token("NotBearer abc123")
        assert token is None

    def test_validate_bearer_format_valid(self):
        """Test bearer token format validation for valid tokens."""
        valid_tokens = [
            "abc123",
            "ABC123xyz",
            "a1b2c3d4e5f6",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",  # JWT-like
            "U3R1ZmYuR29lcy5IZXJl",  # Base64-like
            "access_token_with_underscores",
            "access-token-with-hyphens",
            "accessTokenWith.Dots",
            "a" * 100,  # Long token
        ]

        for token in valid_tokens:
            assert validate_bearer_format(token), f"Token should be valid: {token}"

    def test_validate_bearer_format_invalid(self):
        """Test bearer token format validation for invalid tokens."""
        invalid_tokens = [
            "",  # Empty
            " ",  # Space only
            "token with spaces",
            "token\twith\ttabs",
            "token\nwith\nnewlines",
            "token with émojis 🔐",
            None,  # None type
        ]

        for token in invalid_tokens:
            assert not validate_bearer_format(token), (
                f"Token should be invalid: {token}"
            )

    def test_create_auth_header(self):
        """Test creation of Authorization header."""
        token = "test_token_123"
        header = create_auth_header(token)
        assert header == "Bearer test_token_123"

        # With special characters (should still work, validation is separate)
        token_with_special = "test token with spaces"
        header = create_auth_header(token_with_special)
        assert header == "Bearer test token with spaces"


class TestBearerTokenIntegration:
    """Test integration between bearer token utility functions."""

    def test_bearer_token_roundtrip(self):
        """Test creating and extracting bearer token."""
        original_token = "test_token_123"

        # Create auth header
        auth_header = create_auth_header(original_token)

        # Extract token back
        extracted_token = extract_bearer_token(auth_header)

        assert extracted_token == original_token

    def test_bearer_token_validation_in_context(self):
        """Test bearer token validation in realistic usage context."""
        # Simulate receiving a token from Authorization header
        auth_header = "Bearer valid_token_123"
        token = extract_bearer_token(auth_header)

        assert token is not None
        assert validate_bearer_format(token)

        # Test creating response header
        response_header = create_auth_header(token)
        assert response_header == auth_header

    def test_invalid_bearer_token_workflow(self):
        """Test workflow with invalid bearer token."""
        # Invalid header format
        auth_header = "Basic invalid_format"
        token = extract_bearer_token(auth_header)

        assert token is None

        # Invalid token format (with spaces)
        invalid_token = "token with spaces"
        assert not validate_bearer_format(invalid_token)

        # But we can still create a header (validation is separate concern)
        header = create_auth_header(invalid_token)
        assert header == "Bearer token with spaces"
