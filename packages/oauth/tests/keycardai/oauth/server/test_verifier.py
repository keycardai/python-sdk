"""Tests for TokenVerifier.verify_token method."""

import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from keycardai.oauth.exceptions import InvalidTokenError, OAuthHttpError
from keycardai.oauth.server._cache import JWKSKey
from keycardai.oauth.server.exceptions import (
    JWKSDiscoveryError,
    JWKSUriValidationError,
    VerifierConfigError,
)
from keycardai.oauth.server.verifier import AccessToken, TokenVerifier
from keycardai.oauth.utils.jwt import JWTAccessToken


class TestTokenVerifierJwksSameOrigin:
    """A discovered jwks_uri must share the issuer's origin (security)."""

    def _verifier_with_discovered_jwks(self, issuer: str, discovered_jwks_uri: str):
        metadata = Mock()
        metadata.jwks_uri = discovered_jwks_uri
        client = Mock()
        client.discover_server_metadata = Mock(return_value=metadata)
        factory = Mock()
        factory.create_client = Mock(return_value=client)
        return TokenVerifier(issuer=issuer, client_factory=factory)

    def test_cross_origin_discovered_jwks_uri_rejected(self):
        verifier = self._verifier_with_discovered_jwks(
            "https://example.com", "https://evil.example.net/.well-known/jwks.json"
        )
        with pytest.raises(JWKSUriValidationError):
            verifier._discover_jwks_uri()

    def test_same_origin_discovered_jwks_uri_accepted(self):
        verifier = self._verifier_with_discovered_jwks(
            "https://example.com", "https://example.com/.well-known/jwks.json"
        )
        assert (
            verifier._discover_jwks_uri()
            == "https://example.com/.well-known/jwks.json"
        )


class TestTokenVerifierKidRequired:
    """A token with no `kid` header is rejected (parity with the TypeScript verifier)."""

    def test_missing_kid_rejected(self):
        verifier = TokenVerifier(
            issuer="https://example.com",
            jwks_uri="https://example.com/.well-known/jwks.json",
        )
        with patch(
            "keycardai.oauth.server.verifier.get_header",
            return_value={"alg": "RS256"},
        ):
            with pytest.raises(InvalidTokenError, match="kid"):
                verifier._get_kid_and_algorithm("token")

    def test_present_kid_accepted(self):
        verifier = TokenVerifier(
            issuer="https://example.com",
            jwks_uri="https://example.com/.well-known/jwks.json",
        )
        with patch(
            "keycardai.oauth.server.verifier.get_header",
            return_value={"alg": "RS256", "kid": "abc"},
        ):
            assert verifier._get_kid_and_algorithm("token") == ("abc", "RS256")


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

    def create_unverified_claims(
        self,
        exp: int = None,
        iss: str = "https://test-issuer.com",
    ) -> dict:
        """Build the unverified-claims dict get_claims would return.

        Mirrors the iss/exp of the mocked JWTAccessToken so the cheap pre-checks
        in verify_token (issuer allowlist, expiration) pass and the flow reaches
        the signature step.
        """
        current_time = int(time.time())
        return {
            "iss": iss,
            "exp": exp if exp is not None else current_time + 3600,
        }

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
        mock_claims = self.create_unverified_claims()

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

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

            mock_get_key.assert_called_once_with(
                "test.jwt.token", issuer="https://test-issuer.com"
            )
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
        mock_claims = self.create_unverified_claims()

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

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
        mock_claims = self.create_unverified_claims(exp=expired_time)

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            with pytest.raises(InvalidTokenError):
                await verifier.verify_token("test.jwt.token")

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
        mock_claims = self.create_unverified_claims(iss="https://wrong-issuer.com")

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            with pytest.raises(InvalidTokenError):
                await verifier.verify_token("test.jwt.token")

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
        mock_claims = self.create_unverified_claims(iss="https://expected-issuer.com")

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

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
        mock_claims = self.create_unverified_claims()

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            with pytest.raises(InvalidTokenError):
                await verifier.verify_token("test.jwt.token")

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
        mock_claims = self.create_unverified_claims()

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

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
        mock_claims = self.create_unverified_claims()

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

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
        mock_claims = self.create_unverified_claims()

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            with pytest.raises(InvalidTokenError):
                await verifier.verify_token("test.jwt.token")

    @pytest.mark.asyncio
    async def test_verify_token_get_verification_key_failure(self):
        """Test handling of verification key retrieval failure."""
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            jwks_uri="https://example.com/.well-known/jwks.json"
        )

        mock_claims = self.create_unverified_claims()

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims):
            mock_get_key.side_effect = Exception("JWKS fetch failed")

            # Key resolution failures are not wrapped; the underlying error
            # propagates rather than surfacing as InvalidTokenError.
            with pytest.raises(Exception, match="JWKS fetch failed"):
                await verifier.verify_token("test.jwt.token")

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
        mock_claims = self.create_unverified_claims()

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.side_effect = ValueError("Invalid JWT signature")

            with pytest.raises(InvalidTokenError):
                await verifier.verify_token("test.jwt.token")

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
        mock_claims = self.create_unverified_claims(iss="https://keycard.ai")

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

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

        with patch('keycardai.oauth.server.verifier.time.time') as mock_time:
            mock_time.return_value = current_time
            mock_jwt_token = self.create_mock_jwt_access_token(exp=current_time - 1)
            mock_claims = self.create_unverified_claims(exp=current_time - 1)

            with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
                 patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
                 patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

                mock_get_key.return_value = mock_key
                mock_parse.return_value = mock_jwt_token

                with pytest.raises(InvalidTokenError):
                    await verifier.verify_token("test.jwt.token")

        # Test token expiring in the future (should be accepted)
        with patch('keycardai.oauth.server.verifier.time.time') as mock_time:
            mock_time.return_value = current_time
            mock_jwt_token = self.create_mock_jwt_access_token(exp=current_time + 1)
            mock_claims = self.create_unverified_claims(exp=current_time + 1)

            with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
                 patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
                 patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

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
        mock_claims = self.create_unverified_claims()

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

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
        mock_claims = self.create_unverified_claims()

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

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
        """A 404 from key resolution propagates (verify no longer swallows it)."""
        verifier = TokenVerifier(
            issuer="https://keycard.cloud",
            enable_multi_zone=True
        )
        mock_claims = self.create_unverified_claims(
            iss="https://invalid-zone-id.keycard.cloud"
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims):
            mock_get_key.side_effect = OAuthHttpError(
                status_code=404,
                response_body="Not Found",
                operation="GET /.well-known/oauth-authorization-server"
            )

            with pytest.raises(OAuthHttpError):
                await verifier.verify_token_for_zone("test.jwt.token", "invalid-zone-id")

            mock_get_key.assert_called_once_with("test.jwt.token", "invalid-zone-id")

    @pytest.mark.asyncio
    async def test_verify_token_for_zone_discovery_error(self):
        """A JWKS discovery error from key resolution propagates."""
        verifier = TokenVerifier(
            issuer="https://keycard.cloud",
            enable_multi_zone=True
        )
        mock_claims = self.create_unverified_claims(
            iss="https://invalid-zone.keycard.cloud"
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims):
            mock_get_key.side_effect = JWKSDiscoveryError(
                "http://invalid.keycard.cloud", "invalid-zone"
            )

            with pytest.raises(JWKSDiscoveryError):
                await verifier.verify_token_for_zone("test.jwt.token", "invalid-zone")

            mock_get_key.assert_called_once_with("test.jwt.token", "invalid-zone")

    @pytest.mark.asyncio
    async def test_verify_token_for_zone_other_http_errors_propagate(self):
        """Test that HTTP errors other than 404 are properly propagated."""
        verifier = TokenVerifier(
            issuer="https://keycard.cloud",
            enable_multi_zone=True
        )
        mock_claims = self.create_unverified_claims(
            iss="https://some-zone-id.keycard.cloud"
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims):
            mock_get_key.side_effect = OAuthHttpError(
                status_code=500,
                response_body="Internal Server Error",
                operation="GET /.well-known/oauth-authorization-server"
            )

            with pytest.raises(OAuthHttpError):
                await verifier.verify_token_for_zone("test.jwt.token", "some-zone-id")

            mock_get_key.assert_called_once_with("test.jwt.token", "some-zone-id")

    @pytest.mark.asyncio
    async def test_verify_token_for_zone_key_resolution_value_error_propagates(self):
        """A ValueError from key resolution propagates unchanged.

        Only ValueErrors from ``parse_jwt_access_token`` (inside ``_verify_token``)
        are wrapped as ``InvalidTokenError``; failures during key resolution are not.
        """
        verifier = TokenVerifier(
            issuer="https://keycard.cloud",
            enable_multi_zone=True
        )
        mock_claims = self.create_unverified_claims(
            iss="https://some-zone-id.keycard.cloud"
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims):
            mock_get_key.side_effect = ValueError(
                "JWT parsing failed"
            )

            with pytest.raises(ValueError, match="JWT parsing failed"):
                await verifier.verify_token_for_zone("test.jwt.token", "some-zone-id")

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
        mock_claims = self.create_unverified_claims(
            iss="https://zone1.keycard.cloud"
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token_for_zone("test.jwt.token", "zone1")

            assert result is not None
            assert isinstance(result, AccessToken)
            assert result.token == "test.jwt.token"
            assert result.client_id == "test-client"
            mock_get_key.assert_called_once_with("test.jwt.token", "zone1")

    @pytest.mark.asyncio
    async def test_untrusted_issuer_rejected_before_key_resolution(self):
        """An untrusted issuer is rejected before any network key resolution."""
        verifier = TokenVerifier(
            issuer="https://trusted.example.com",
            jwks_uri="https://trusted.example.com/.well-known/jwks.json",
        )
        mock_claims = self.create_unverified_claims(
            iss="https://evil.example.com"
        )

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims):
            with pytest.raises(InvalidTokenError):
                await verifier.verify_token("test.jwt.token")

            mock_get_key.assert_not_called()

    @pytest.mark.asyncio
    async def test_issuer_allowlist_accepts_any_trusted_issuer(self):
        """An allowlist accepts a token whose iss is any member, rejects others."""
        verifier = TokenVerifier(
            issuer=["https://a.example.com", "https://b.example.com"],
            jwks_uri="https://a.example.com/.well-known/jwks.json",
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256",
        )

        # A token issued by an allowlisted issuer (the second one) succeeds.
        trusted_token = self.create_mock_jwt_access_token(iss="https://b.example.com")
        trusted_claims = self.create_unverified_claims(iss="https://b.example.com")
        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=trusted_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = trusted_token

            result = await verifier.verify_token("test.jwt.token")
            assert isinstance(result, AccessToken)

        # A token issued by an issuer not on the allowlist is rejected.
        untrusted_claims = self.create_unverified_claims(iss="https://c.example.com")
        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=untrusted_claims):
            with pytest.raises(InvalidTokenError):
                await verifier.verify_token("test.jwt.token")

            mock_get_key.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_token_returns_access_token_never_none(self):
        """verify_token returns an AccessToken on success (never None)."""
        verifier = TokenVerifier(
            issuer="https://test-issuer.com",
            jwks_uri="https://example.com/.well-known/jwks.json",
        )

        mock_key = JWKSKey(
            key="mock-public-key",
            timestamp=time.time(),
            algorithm="RS256",
        )
        mock_jwt_token = self.create_mock_jwt_access_token()
        mock_claims = self.create_unverified_claims()

        with patch.object(verifier, '_get_verification_key', new_callable=AsyncMock) as mock_get_key, \
             patch('keycardai.oauth.server.verifier.get_claims', return_value=mock_claims), \
             patch('keycardai.oauth.server.verifier.parse_jwt_access_token') as mock_parse:

            mock_get_key.return_value = mock_key
            mock_parse.return_value = mock_jwt_token

            result = await verifier.verify_token("test.jwt.token")

            assert result is not None
            assert isinstance(result, AccessToken)


class TestTokenVerifierCacheKnobs:
    """Cache knobs (key_ttl / discovery_ttl / fetch_timeout) and the cache_ttl alias."""

    def test_default_knobs(self):
        verifier = TokenVerifier(issuer="https://example.com")
        assert verifier.key_ttl == 300
        assert verifier.discovery_ttl == 3600
        assert verifier.fetch_timeout == 10.0

    def test_custom_knobs(self):
        verifier = TokenVerifier(
            issuer="https://example.com",
            key_ttl=60,
            discovery_ttl=120,
            fetch_timeout=2.5,
        )
        assert verifier.key_ttl == 60
        assert verifier.discovery_ttl == 120
        assert verifier.fetch_timeout == 2.5

    def test_cache_ttl_is_deprecated_alias_for_key_ttl(self):
        with pytest.warns(DeprecationWarning, match="cache_ttl"):
            verifier = TokenVerifier(issuer="https://example.com", cache_ttl=42)
        assert verifier.key_ttl == 42
        assert verifier.cache_ttl == 42


class TestTokenVerifierDiscoveryTtl:
    """The discovered jwks_uri is cached, then re-discovered after discovery_ttl."""

    def _verifier(self, discovery_ttl: int):
        metadata = Mock()
        metadata.jwks_uri = "https://example.com/.well-known/jwks.json"
        client = Mock()
        client.discover_server_metadata = Mock(return_value=metadata)
        factory = Mock()
        factory.create_client = Mock(return_value=client)
        verifier = TokenVerifier(
            issuer="https://example.com",
            client_factory=factory,
            discovery_ttl=discovery_ttl,
        )
        return verifier, client

    def test_cached_within_ttl(self):
        verifier, client = self._verifier(discovery_ttl=3600)
        with patch("keycardai.oauth.server.verifier.time.time", return_value=1000.0):
            verifier._discover_jwks_uri()
            verifier._discover_jwks_uri()
        assert client.discover_server_metadata.call_count == 1

    def test_rediscovers_after_ttl(self):
        verifier, client = self._verifier(discovery_ttl=100)
        with patch("keycardai.oauth.server.verifier.time.time") as mock_time:
            mock_time.return_value = 1000.0
            verifier._discover_jwks_uri()
            mock_time.return_value = 1101.0
            verifier._discover_jwks_uri()
        assert client.discover_server_metadata.call_count == 2


class TestTokenVerifierInflightDedup:
    """Concurrent cold-cache lookups for the same kid share one JWKS fetch."""

    @pytest.mark.asyncio
    async def test_concurrent_fetches_share_one_fetch(self):
        verifier = TokenVerifier(
            issuer="https://example.com",
            jwks_uri="https://example.com/.well-known/jwks.json",
        )

        call_count = 0

        async def slow_get_jwks_key(kid, jwks_uri, timeout=None):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return "mock-public-key"

        with patch(
            "keycardai.oauth.server.verifier.get_header",
            return_value={"alg": "RS256", "kid": "abc"},
        ), patch(
            "keycardai.oauth.server.verifier.get_jwks_key",
            side_effect=slow_get_jwks_key,
        ):
            results = await asyncio.gather(
                verifier._get_verification_key("t1"),
                verifier._get_verification_key("t2"),
                verifier._get_verification_key("t3"),
            )

        assert call_count == 1
        assert all(r.key == "mock-public-key" for r in results)
        assert all(r.algorithm == "RS256" for r in results)
