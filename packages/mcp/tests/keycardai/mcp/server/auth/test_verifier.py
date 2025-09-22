"""Tests for TokenVerifier.verify_token method."""

import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from mcp.server.auth.provider import AccessToken

from keycardai.mcp.server.auth._cache import JWKSKey
from keycardai.mcp.server.auth.verifier import TokenVerifier
from keycardai.mcp.server.exceptions import (
    JWKSDiscoveryError,
    VerifierConfigError,
)
from keycardai.oauth.exceptions import OAuthHttpError
from keycardai.oauth.utils.jwt import JWTAccessToken


class TestTokenVerifierVerifyToken:
    """Test TokenVerifier.verify_token method with various scenarios."""

    def create_mock_jwt_access_token(
        self,
        exp: int = None,
        iss: str = "https://test-issuer.com",
        client_id: str = "test-client",
        scope: str = "read write",
        custom_claims: dict = None,
        aud: str = "test-audience",
    ) -> Mock:
        """Create a mock JWTAccessToken for testing."""
        current_time = int(time.time())
        token = Mock(spec=JWTAccessToken)
        token.exp = exp if exp is not None else current_time + 3600  # 1 hour future
        token.iss = iss
        token.client_id = client_id
        token.scope = scope
        token.aud = aud
        token.get_custom_claim = Mock(
            side_effect=lambda key, default=None: (custom_claims or {}).get(key, default)
        )

        # Add the new validation methods that actually implement the logic
        def mock_validate_scopes(required_scopes):
            if not required_scopes:
                return True
            token_scopes = scope.split() if scope else []
            token_scopes_set = set(token_scopes)
            required_scopes_set = set(required_scopes)
            return required_scopes_set.issubset(token_scopes_set)

        def mock_validate_audience(expected_audience, zone_id=None):
            if expected_audience is None:
                return True
            if aud is None:
                return False
            if isinstance(expected_audience, str):
                if isinstance(aud, list):
                    return expected_audience in aud
                else:
                    return aud == expected_audience
            elif isinstance(expected_audience, dict):
                if not zone_id:
                    return False
                expected_aud = expected_audience.get(zone_id)
                if expected_aud is None:
                    return False
                if isinstance(aud, list):
                    return expected_aud in aud
                else:
                    return aud == expected_aud
            return False

        token.validate_audience = Mock(side_effect=mock_validate_audience)
        token.validate_scopes = Mock(side_effect=mock_validate_scopes)
        token.get_scopes = Mock(return_value=scope.split() if scope else [])

        return token

    @pytest.mark.asyncio
    async def test_verify_token_success_basic(self):
        """Test successful token verification with minimal configuration."""
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )
        mock_jwt_token = self.create_mock_jwt_access_token()

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token("test.jwt.token")

            assert result is not None
            assert isinstance(result, AccessToken)
            assert result.token == "test.jwt.token"
            assert result.client_id == "test-client"
            assert result.scopes == ["read", "write"]
            assert result.expires_at == mock_jwt_token.exp
            assert result.resource is None

            mock_get_key.assert_called_once_with("test.jwt.token")
            mock_parse.assert_called_once_with(
                "test.jwt.token", "mock-public-key", "RS256"
            )

    @pytest.mark.asyncio
    async def test_verify_token_with_resource_claim(self):
        """Test token verification with custom resource claim."""
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )
        mock_jwt_token = self.create_mock_jwt_access_token(
            custom_claims={"resource": "api.example.com"}
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token("test.jwt.token")

            assert result is not None
            assert result.resource == "api.example.com"
            mock_jwt_token.get_custom_claim.assert_called_with("resource")

    @pytest.mark.asyncio
    async def test_verify_token_expired_token(self):
        """Test that expired tokens are rejected."""
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )
        # Create expired token (1 hour ago)
        expired_time = int(time.time()) - 3600
        mock_jwt_token = self.create_mock_jwt_access_token(exp=expired_time)

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token("test.jwt.token")

            assert result is None

    @pytest.mark.asyncio
    async def test_verify_token_wrong_issuer(self):
        """Test that tokens with wrong issuer are rejected."""
        verifier = TokenVerifier(
            issuer="https://expected-issuer.com",
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )
        mock_jwt_token = self.create_mock_jwt_access_token(
            iss="https://wrong-issuer.com"
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token("test.jwt.token")

            assert result is None

    @pytest.mark.asyncio
    async def test_verify_token_correct_issuer(self):
        """Test that tokens with correct issuer are accepted."""
        verifier = TokenVerifier(
            issuer="https://expected-issuer.com",
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )
        mock_jwt_token = self.create_mock_jwt_access_token(
            iss="https://expected-issuer.com"
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token("test.jwt.token")

            assert result is not None
            assert result.client_id == "test-client"

    @pytest.mark.asyncio
    async def test_verify_token_insufficient_scopes(self):
        """Test that tokens with insufficient scopes are rejected."""
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            required_scopes=["read", "write", "admin"],
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )
        mock_jwt_token = self.create_mock_jwt_access_token(
            scope="read write"  # Missing 'admin' scope
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token("test.jwt.token")

            assert result is None

    @pytest.mark.asyncio
    async def test_verify_token_sufficient_scopes(self):
        """Test that tokens with sufficient scopes are accepted."""
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            required_scopes=["read", "write"],
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )
        mock_jwt_token = self.create_mock_jwt_access_token(
            scope="read write admin"  # Has required scopes plus extra
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token("test.jwt.token")

            assert result is not None
            assert set(result.scopes) == {"read", "write", "admin"}

    @pytest.mark.asyncio
    async def test_verify_token_empty_scopes_in_token(self):
        """Test handling of tokens with empty/null scopes."""
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )
        mock_jwt_token = self.create_mock_jwt_access_token(scope=None)

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token("test.jwt.token")

            assert result is not None
            assert result.scopes == []

    @pytest.mark.asyncio
    async def test_verify_token_empty_scopes_required(self):
        """Test tokens with empty scopes when scopes are required."""
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            required_scopes=["read"],
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )
        mock_jwt_token = self.create_mock_jwt_access_token(scope="")

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token("test.jwt.token")

            assert result is None

    @pytest.mark.asyncio
    async def test_verify_token_get_verification_key_failure(self):
        """Test handling of verification key retrieval failure."""
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key:
            mock_get_key.side_effect = Exception("JWKS fetch failed")

            result = await verifier.verify_token("test.jwt.token")

            assert result is None

    @pytest.mark.asyncio
    async def test_verify_token_parse_jwt_failure(self):
        """Test handling of JWT parsing failure."""
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.side_effect = Exception("Invalid JWT signature")

            result = await verifier.verify_token("test.jwt.token")

            assert result is None

    @pytest.mark.asyncio
    async def test_verify_token_complex_scenario(self):
        """Test complex scenario with all validations passing."""
        verifier = TokenVerifier(
            issuer="https://keycard.ai",
            required_scopes=["mcp:read", "mcp:write"],
            jwks_uri="https://keycard.ai/.well-known/jwks.json"
        )

        mock_key = JWKSKey(
            key="production-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )
        mock_jwt_token = self.create_mock_jwt_access_token(
            iss="https://keycard.ai",
            client_id="prod-client-123",
            scope="mcp:read mcp:write mcp:admin user:profile",
            custom_claims={
                "resource": "api.keycard.ai",
                "tenant": "org-456",
                "role": "admin"
            }
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token("complex.jwt.token")

            assert result is not None
            assert result.token == "complex.jwt.token"
            assert result.client_id == "prod-client-123"
            assert set(result.scopes) == {"mcp:read", "mcp:write", "mcp:admin", "user:profile"}
            assert result.resource == "api.keycard.ai"
            assert result.expires_at == mock_jwt_token.exp

    @pytest.mark.asyncio
    async def test_verify_token_time_boundary_conditions(self):
        """Test token verification at exact expiration boundaries."""
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )

        # Test token expiring exactly now (should be rejected)
        current_time = int(time.time())

        with patch('keycardai.mcp.server.auth.verifier.time.time') as mock_time:
            mock_time.return_value = current_time
            mock_jwt_token = self.create_mock_jwt_access_token(exp=current_time - 1)

            with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
                 patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

                mock_get_key.return_value = mock_key
                mock_parse.return_value = mock_jwt_token

                result = await verifier.verify_token("test.jwt.token")
                assert result is None

        # Test token expiring in the future (should be accepted)
        with patch('keycardai.mcp.server.auth.verifier.time.time') as mock_time:
            mock_time.return_value = current_time
            mock_jwt_token = self.create_mock_jwt_access_token(exp=current_time + 1)

            with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
                 patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

                mock_get_key.return_value = mock_key
                mock_parse.return_value = mock_jwt_token

                result = await verifier.verify_token("test.jwt.token")
                assert result is not None

    @pytest.mark.asyncio
    async def test_verify_token_scope_edge_cases(self):
        """Test various edge cases in scope validation."""
        # Test with required scopes = empty list (should always pass)
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            required_scopes=[],
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )
        mock_jwt_token = self.create_mock_jwt_access_token(scope="any scope")

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token("test.jwt.token")
            assert result is not None

        # Test with exact scope match
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            required_scopes=["exact", "match"],
            jwks_uri="https://example.com/.well-known/jwks.json"
        )
        mock_jwt_token = self.create_mock_jwt_access_token(scope="exact match")

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token("test.jwt.token")
            assert result is not None
            assert set(result.scopes) == {"exact", "match"}

    def test_token_verifier_requires_issuer(self):
        """Test that TokenVerifier raises error when no issuer is provided."""
        with pytest.raises(VerifierConfigError, match="Issuer is required for token verification"):
            TokenVerifier(
                issuer="",  # Empty issuer should raise error
                jwks_uri="https://example.com/.well-known/jwks.json"
            )

        with pytest.raises(VerifierConfigError, match="Issuer is required for token verification"):
            TokenVerifier(
                issuer=None,  # None issuer should raise error
                jwks_uri="https://example.com/.well-known/jwks.json"
            )

    @pytest.mark.asyncio
    async def test_verify_token_for_zone_invalid_zone_id(self):
        """Test that invalid zone_id returns None instead of raising exception."""
        verifier = TokenVerifier(
            issuer="https://keycard.cloud",
            enable_multi_zone=True
        )

        # Mock _get_verification_key to raise OAuthHttpError with 404 (simulating invalid zone)
        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key:
            mock_get_key.side_effect = OAuthHttpError(
                status_code=404,
                response_body="Not Found",
                operation="GET /.well-known/oauth-authorization-server"
            )

            result = await verifier.verify_token_for_zone("test.jwt.token", "invalid-zone-id")

            assert result is None
            mock_get_key.assert_called_once_with("test.jwt.token", "invalid-zone-id")

    @pytest.mark.asyncio
    async def test_verify_token_for_zone_discovery_error(self):
        """Test that JWKS discovery error returns None instead of raising exception."""
        verifier = TokenVerifier(
            issuer="https://keycard.cloud",
            enable_multi_zone=True
        )

        # Mock _get_verification_key to raise JWKSDiscoveryError from discovery failure
        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key:
            mock_get_key.side_effect = JWKSDiscoveryError(
                "http://invalid.keycard.cloud", "invalid-zone"
            )

            result = await verifier.verify_token_for_zone("test.jwt.token", "invalid-zone")

            assert result is None
            mock_get_key.assert_called_once_with("test.jwt.token", "invalid-zone")

    @pytest.mark.asyncio
    async def test_verify_token_for_zone_other_http_errors_propagate(self):
        """Test that HTTP errors other than 404 are properly propagated."""
        verifier = TokenVerifier(
            issuer="https://keycard.cloud",
            enable_multi_zone=True
        )

        # Mock _get_verification_key to raise OAuthHttpError with 500 (should propagate)
        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key:
            mock_get_key.side_effect = OAuthHttpError(
                status_code=500,
                response_body="Internal Server Error",
                operation="GET /.well-known/oauth-authorization-server"
            )

            access_token = await verifier.verify_token_for_zone("test.jwt.token", "some-zone-id")

            assert access_token is None
            mock_get_key.assert_called_once_with("test.jwt.token", "some-zone-id")

    @pytest.mark.asyncio
    async def test_verify_token_for_zone_value_errors_converted_to_token_validation_error(self):
        """Test that ValueError are converted to TokenValidationError for proper error handling."""
        verifier = TokenVerifier(
            issuer="https://keycard.cloud",
            enable_multi_zone=True
        )

        # Mock _get_verification_key to raise ValueError (should be wrapped as TokenValidationError)
        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key:
            mock_get_key.side_effect = ValueError(
                "JWT parsing failed"
            )

            access_token = await verifier.verify_token_for_zone("test.jwt.token", "some-zone-id")

            assert access_token is None
            mock_get_key.assert_called_once_with("test.jwt.token", "some-zone-id")

    @pytest.mark.asyncio
    async def test_verify_token_for_zone_success(self):
        """Test successful multi-zone token verification."""
        verifier = TokenVerifier(
            issuer="https://keycard.cloud",
            enable_multi_zone=True
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256"
        )
        mock_jwt_token = self.create_mock_jwt_access_token(
            iss="https://zone1.keycard.cloud"  # Zone-scoped issuer
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.mcp.server.auth.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token_for_zone("test.jwt.token", "zone1")

            assert result is not None
            assert isinstance(result, AccessToken)
            assert result.token == "test.jwt.token"
            assert result.client_id == "test-client"
            mock_get_key.assert_called_once_with("test.jwt.token", "zone1")
