"""Tests for JWT utility functions."""

import base64
import json
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from authlib.jose import JsonWebKey, JsonWebSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from keycardai.oauth.utils.jwt import (
    JWTAccessToken,
    _decode_jwt_part,
    _split_jwt_token,
    decode_and_verify_jwt,
    extract_scopes,
    get_claims,
    get_header,
    get_jwks_key,
    get_verification_key,
    parse_jwt_access_token,
)


class TestJWTTokenSplitting:
    """Test JWT token splitting functionality."""

    def test_split_jwt_token_valid(self):
        """Test splitting a valid JWT token."""
        token = "header.payload.signature"
        header, payload, signature = _split_jwt_token(token)

        assert header == "header"
        assert payload == "payload"
        assert signature == "signature"

    def test_split_jwt_token_invalid_format(self):
        """Test splitting an invalid JWT token format."""
        invalid_tokens = [
            "invalid",
            "header.payload",
            "header.payload.signature.extra",
            "",
            ".",  # This becomes ["", ""] which has 2 parts, not 3
            ".."
        ]

        for token in invalid_tokens:
            with pytest.raises(ValueError, match="Invalid JWT token format"):
                _split_jwt_token(token)



class TestJWTPartDecoding:
    """Test JWT part decoding functionality."""

    def test_decode_jwt_part_valid(self):
        """Test decoding valid base64-encoded JWT parts."""
        # Create a valid JSON payload
        data = {"test": "value", "number": 123}
        json_str = json.dumps(data)
        encoded = base64.urlsafe_b64encode(json_str.encode()).decode().rstrip("=")

        result = _decode_jwt_part(encoded)
        assert result == data

    def test_decode_jwt_part_with_padding(self):
        """Test decoding JWT part that needs padding."""
        # Create data that will need padding when base64 encoded
        data = {"sub": "user123"}
        json_str = json.dumps(data)
        encoded = base64.urlsafe_b64encode(json_str.encode()).decode().rstrip("=")

        result = _decode_jwt_part(encoded)
        assert result == data

    def test_decode_jwt_part_invalid_base64(self):
        """Test decoding invalid base64 data."""
        invalid_data = "invalid!@#$%"

        with pytest.raises(ValueError):
            _decode_jwt_part(invalid_data)

    def test_decode_jwt_part_invalid_json(self):
        """Test decoding valid base64 but invalid JSON."""
        invalid_json = base64.urlsafe_b64encode(b"not json").decode()

        with pytest.raises(ValueError, match="Failed to decode JWT part"):
            _decode_jwt_part(invalid_json)

    def test_decode_jwt_part_non_dict_json(self):
        """Test decoding valid JSON that's not a dictionary."""
        non_dict_json = json.dumps(["array", "data"])
        encoded = base64.urlsafe_b64encode(non_dict_json.encode()).decode()

        result = _decode_jwt_part(encoded)
        assert result == {}


class TestGetClaims:
    """Test claims extraction functionality."""

    def create_jwt_token(self, header: dict, payload: dict) -> str:
        """Helper to create a JWT token for testing."""
        header_b64 = (
            base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
        )
        payload_b64 = (
            base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        )
        return f"{header_b64}.{payload_b64}.signature"

    def test_get_claims_valid_token(self):
        """Test extracting claims from a valid JWT token."""
        payload = {
            "sub": "user123",
            "iss": "https://example.com",
            "aud": "client123",
            "exp": 1234567890,
            "iat": 1234567800,
            "client_id": "test_client",
        }
        header = {"alg": "RS256", "typ": "JWT"}
        token = self.create_jwt_token(header, payload)

        claims = get_claims(token)
        assert claims == payload

    def test_get_claims_invalid_token(self):
        """Test extracting claims from an invalid JWT token."""
        with pytest.raises(ValueError, match="Invalid JWT token format"):
            get_claims("invalid.token")

    def test_get_header_valid_token(self):
        """Test extracting header from a valid JWT token."""
        header = {"alg": "RS256", "typ": "JWT", "kid": "key1"}
        payload = {"sub": "user123"}
        token = self.create_jwt_token(header, payload)

        extracted_header = get_header(token)
        assert extracted_header == header

    def test_get_header_invalid_token(self):
        """Test extracting header from an invalid JWT token."""
        with pytest.raises(ValueError, match="Invalid JWT token format"):
            get_header("invalid")

    def test_get_header_none_algorithm_rejected(self):
        """Test that tokens with 'none' algorithm are rejected for security."""
        header = {"alg": "none", "typ": "JWT"}
        payload = {"sub": "user123"}
        token = self.create_jwt_token(header, payload)

        with pytest.raises(ValueError, match="none algorithm is not supported"):
            get_header(token)


class TestExtractScopes:
    """Test scope extraction functionality."""

    def test_extract_scopes_from_scope_string(self):
        """Test extracting scopes from space-separated scope string."""
        claims = {"scope": "read write admin"}
        scopes = extract_scopes(claims)
        assert scopes == ["read", "write", "admin"]

    def test_extract_scopes_from_scp_list(self):
        """Test extracting scopes from scp list."""
        claims = {"scp": ["read", "write", "admin"]}
        scopes = extract_scopes(claims)
        assert scopes == ["read", "write", "admin"]

    def test_extract_scopes_empty_scope(self):
        """Test extracting scopes from empty scope string."""
        claims = {"scope": ""}
        scopes = extract_scopes(claims)
        # Empty string split() returns empty list, not list with empty string
        assert scopes == []

    def test_extract_scopes_no_scope_claims(self):
        """Test extracting scopes when no scope claims exist."""
        claims = {"sub": "user123", "iss": "example.com"}
        scopes = extract_scopes(claims)
        assert scopes == []

    def test_extract_scopes_both_claims_prefer_scope(self):
        """Test that 'scope' claim is preferred over 'scp' when both exist."""
        claims = {"scope": "read write", "scp": ["admin"]}
        scopes = extract_scopes(claims)
        assert scopes == ["read", "write"]

    def test_extract_scopes_invalid_type(self):
        """Test extracting scopes from invalid claim types."""
        claims = {"scope": 123}  # Invalid type
        scopes = extract_scopes(claims)
        assert scopes == []


class TestJWTAccessToken:
    """Test JWTAccessToken model functionality."""

    def create_valid_token_data(self) -> dict:
        """Helper to create valid token data."""
        return {
            "iss": "https://example.com",
            "sub": "user123",
            "aud": "client123",
            "exp": 1234567890,
            "iat": 1234567800,
            "client_id": "test_client",
            "scope": "read write",
            "custom_claims": {"role": "admin", "tenant": "org1"},
            "_raw": "header.payload.signature",
        }

    def test_jwt_access_token_creation(self):
        """Test creating a JWTAccessToken instance."""
        data = self.create_valid_token_data()
        token = JWTAccessToken(**data)

        assert token.iss == "https://example.com"
        assert token.sub == "user123"
        assert token.aud == "client123"
        assert token.exp == 1234567890
        assert token.iat == 1234567800
        assert token.client_id == "test_client"
        assert token.scope == "read write"
        assert token.custom_claims == {"role": "admin", "tenant": "org1"}

    def test_get_custom_claim(self):
        """Test getting custom claims."""
        data = self.create_valid_token_data()
        token = JWTAccessToken(**data)

        assert token.get_custom_claim("role") == "admin"
        assert token.get_custom_claim("nonexistent") is None
        assert token.get_custom_claim("nonexistent", "default") == "default"

    def test_has_custom_claim(self):
        """Test checking if custom claims exist."""
        data = self.create_valid_token_data()
        token = JWTAccessToken(**data)

        assert token.has_custom_claim("role") is True
        assert token.has_custom_claim("nonexistent") is False

    def test_get_all_claims(self):
        """Test getting all claims as dictionary."""
        data = self.create_valid_token_data()
        token = JWTAccessToken(**data)

        all_claims = token.get_all_claims()

        expected_keys = {
            "iss",
            "sub",
            "aud",
            "exp",
            "iat",
            "client_id",
            "scope",
            "role",
            "tenant",
        }
        assert set(all_claims.keys()) == expected_keys
        assert all_claims["role"] == "admin"
        assert all_claims["tenant"] == "org1"

    def test_get_all_claims_optional_fields(self):
        """Test getting all claims with optional fields."""
        data = self.create_valid_token_data()
        data["jti"] = "token123"
        data["authorization_details"] = [{"type": "payment", "amount": "100"}]
        token = JWTAccessToken(**data)

        all_claims = token.get_all_claims()

        assert all_claims["jti"] == "token123"
        assert all_claims["authorization_details"] == [
            {"type": "payment", "amount": "100"}
        ]


class TestJWTVerification:
    """Test JWT verification functionality."""

    @patch("keycardai.oauth.utils.jwt.JsonWebToken")
    def test_decode_and_verify_jwt_success(self, mock_jwt_class):
        """Test successful JWT verification."""
        mock_jwt = Mock()
        mock_jwt.decode.return_value = {"sub": "user123", "iss": "example.com"}
        mock_jwt_class.return_value = mock_jwt

        result = decode_and_verify_jwt("token", "key", "RS256")

        assert result == {"sub": "user123", "iss": "example.com"}
        mock_jwt_class.assert_called_once_with(["RS256"])
        mock_jwt.decode.assert_called_once_with("token", "key")

    @patch("keycardai.oauth.utils.jwt.JsonWebToken")
    def test_decode_and_verify_jwt_failure(self, mock_jwt_class):
        """Test JWT verification failure."""
        mock_jwt = Mock()
        mock_jwt.decode.side_effect = Exception("Invalid signature")
        mock_jwt_class.return_value = mock_jwt

        with pytest.raises(ValueError, match="JWT verification failed"):
            decode_and_verify_jwt("token", "key", "RS256")

    @patch("keycardai.oauth.utils.jwt.decode_and_verify_jwt")
    def test_parse_jwt_access_token_success(self, mock_verify):
        """Test successful JWT access token parsing."""
        mock_verify.return_value = {
            "iss": "https://example.com",
            "sub": "user123",
            "aud": "client123",
            "exp": 1234567890,
            "iat": 1234567800,
            "client_id": "test_client",
            "scope": "read write",
            "custom_field": "custom_value",
        }

        token = parse_jwt_access_token("jwt_token", "key")

        assert isinstance(token, JWTAccessToken)
        assert token.iss == "https://example.com"
        assert token.sub == "user123"
        assert token.scope == "read write"
        assert token.custom_claims == {"custom_field": "custom_value"}

    @patch("keycardai.oauth.utils.jwt.decode_and_verify_jwt")
    def test_parse_jwt_access_token_missing_claims(self, mock_verify):
        """Test JWT parsing with missing required claims."""
        mock_verify.return_value = {
            "iss": "https://example.com",
            "sub": "user123",
            # Missing required claims: aud, exp, iat, client_id
        }

        with pytest.raises(ValueError, match="Missing required claims"):
            parse_jwt_access_token("jwt_token", "key")

    @patch("keycardai.oauth.utils.jwt.decode_and_verify_jwt")
    def test_parse_jwt_access_token_with_scp_claim(self, mock_verify):
        """Test JWT parsing with scp claim instead of scope."""
        mock_verify.return_value = {
            "iss": "https://example.com",
            "sub": "user123",
            "aud": "client123",
            "exp": 1234567890,
            "iat": 1234567800,
            "client_id": "test_client",
            "scp": ["read", "write", "admin"],
        }

        token = parse_jwt_access_token("jwt_token", "key")

        assert token.scope == "read write admin"


class TestJWKSKeyFetching:
    """Test JWKS key fetching functionality."""

    @pytest.mark.asyncio
    @patch("keycardai.oauth.utils.jwt.get_header")
    @patch("keycardai.oauth.utils.jwt.get_jwks_key")
    async def test_get_verification_key_success(self, mock_get_jwks, mock_get_header):
        """Test successful verification key retrieval."""
        mock_get_header.return_value = {"kid": "key1", "alg": "RS256"}
        mock_get_jwks.return_value = (
            "-----BEGIN PUBLIC KEY-----\nkey_data\n-----END PUBLIC KEY-----"
        )

        key = await get_verification_key(
            "token", "https://example.com/.well-known/jwks.json"
        )

        assert key == "-----BEGIN PUBLIC KEY-----\nkey_data\n-----END PUBLIC KEY-----"
        mock_get_header.assert_called_once_with("token")
        mock_get_jwks.assert_called_once_with(
            "key1", "https://example.com/.well-known/jwks.json"
        )

    @pytest.mark.asyncio
    @patch("keycardai.oauth.utils.jwt.get_header")
    async def test_get_verification_key_failure(self, mock_get_header):
        """Test verification key retrieval failure."""
        mock_get_header.side_effect = Exception("Invalid token")

        with pytest.raises(ValueError, match="Failed to extract key ID from token"):
            await get_verification_key(
                "token", "https://example.com/.well-known/jwks.json"
            )

    @pytest.mark.asyncio
    @patch("keycardai.oauth.utils.jwt.HttpxAsyncTransport")
    @patch("keycardai.oauth.utils.jwt.ClientConfig")
    @patch("keycardai.oauth.utils.jwt.JsonWebKey")
    async def test_get_jwks_key_with_kid(
        self, mock_jwk_class, mock_config_class, mock_transport_class
    ):
        """Test JWKS key fetching with specific key ID."""
        # Mock response
        mock_response = Mock()
        mock_response.status = 200
        mock_response.body = json.dumps(
            {
                "keys": [
                    {"kid": "key1", "kty": "RSA", "use": "sig"},
                    {"kid": "key2", "kty": "RSA", "use": "sig"},
                ]
            }
        ).encode()

        # Mock transport
        mock_transport = AsyncMock()
        mock_transport.request_raw.return_value = mock_response
        mock_transport_class.return_value = mock_transport

        # Mock JWK
        mock_jwk = Mock()
        mock_jwk.get_public_key.return_value = "public_key_pem"
        mock_jwk_class.import_key.return_value = mock_jwk

        key = await get_jwks_key("key1", "https://example.com/.well-known/jwks.json")

        assert key == "public_key_pem"
        mock_jwk_class.import_key.assert_called_once_with(
            {"kid": "key1", "kty": "RSA", "use": "sig"}
        )

    @pytest.mark.asyncio
    @patch("keycardai.oauth.utils.jwt.HttpxAsyncTransport")
    @patch("keycardai.oauth.utils.jwt.ClientConfig")
    async def test_get_jwks_key_key_not_found(
        self, mock_config_class, mock_transport_class
    ):
        """Test JWKS key fetching when key ID not found."""
        mock_response = Mock()
        mock_response.status = 200
        mock_response.body = json.dumps(
            {"keys": [{"kid": "other_key", "kty": "RSA"}]}
        ).encode()

        mock_transport = AsyncMock()
        mock_transport.request_raw.return_value = mock_response
        mock_transport_class.return_value = mock_transport

        with pytest.raises(ValueError, match="Key ID 'key1' not found"):
            await get_jwks_key("key1", "https://example.com/.well-known/jwks.json")

    @pytest.mark.asyncio
    @patch("keycardai.oauth.utils.jwt.HttpxAsyncTransport")
    @patch("keycardai.oauth.utils.jwt.ClientConfig")
    async def test_get_jwks_key_http_error(
        self, mock_config_class, mock_transport_class
    ):
        """Test JWKS key fetching with HTTP error."""
        mock_response = Mock()
        mock_response.status = 404

        mock_transport = AsyncMock()
        mock_transport.request_raw.return_value = mock_response
        mock_transport_class.return_value = mock_transport

        with pytest.raises(ValueError, match="JWKS endpoint returned status 404"):
            await get_jwks_key("key1", "https://example.com/.well-known/jwks.json")

    @pytest.mark.asyncio
    @patch("keycardai.oauth.utils.jwt.HttpxAsyncTransport")
    @patch("keycardai.oauth.utils.jwt.ClientConfig")
    @patch("keycardai.oauth.utils.jwt.JsonWebKey")
    async def test_get_jwks_key_single_key_no_kid(
        self, mock_jwk_class, mock_config_class, mock_transport_class
    ):
        """Test JWKS key fetching with single key and no kid parameter."""
        mock_response = Mock()
        mock_response.status = 200
        mock_response.body = json.dumps(
            {"keys": [{"kty": "RSA", "use": "sig"}]}
        ).encode()

        mock_transport = AsyncMock()
        mock_transport.request_raw.return_value = mock_response
        mock_transport_class.return_value = mock_transport

        mock_jwk = Mock()
        mock_jwk.get_public_key.return_value = "public_key_pem"
        mock_jwk_class.import_key.return_value = mock_jwk

        key = await get_jwks_key(None, "https://example.com/.well-known/jwks.json")

        assert key == "public_key_pem"
        mock_jwk_class.import_key.assert_called_once_with({"kty": "RSA", "use": "sig"})

    @pytest.mark.asyncio
    @patch("keycardai.oauth.utils.jwt.HttpxAsyncTransport")
    @patch("keycardai.oauth.utils.jwt.ClientConfig")
    async def test_get_jwks_key_multiple_keys_no_kid(
        self, mock_config_class, mock_transport_class
    ):
        """Test JWKS key fetching with multiple keys but no kid parameter."""
        mock_response = Mock()
        mock_response.status = 200
        mock_response.body = json.dumps(
            {"keys": [{"kty": "RSA", "use": "sig"}, {"kty": "RSA", "use": "sig"}]}
        ).encode()

        mock_transport = AsyncMock()
        mock_transport.request_raw.return_value = mock_response
        mock_transport_class.return_value = mock_transport

        with pytest.raises(ValueError, match="Multiple keys in JWKS but no key ID"):
            await get_jwks_key(None, "https://example.com/.well-known/jwks.json")

    @pytest.mark.asyncio
    @patch("keycardai.oauth.utils.jwt.HttpxAsyncTransport")
    @patch("keycardai.oauth.utils.jwt.ClientConfig")
    async def test_get_jwks_key_empty_keys_array(
        self, mock_config_class, mock_transport_class
    ):
        """Test JWKS key fetching with empty keys array."""
        mock_response = Mock()
        mock_response.status = 200
        mock_response.body = json.dumps({"keys": []}).encode()

        mock_transport = AsyncMock()
        mock_transport.request_raw.return_value = mock_response
        mock_transport_class.return_value = mock_transport

        with pytest.raises(ValueError, match="No keys found in JWKS"):
            await get_jwks_key("key1", "https://example.com/.well-known/jwks.json")


@pytest.fixture
def rsa_key_pair():
    """Generate an RSA key pair for testing."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )

    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

    return {
        "private_key": private_key,
        "public_key": public_key,
        "private_pem": private_pem.decode('utf-8'),
        "public_pem": public_pem.decode('utf-8'),
        "kid": "test-key-1"
    }


@pytest.fixture
def jwt_token_factory(rsa_key_pair):
    """Factory function to create JWT tokens with various claims."""
    def create_token(claims: dict, kid: str = None, algorithm: str = "RS256") -> str:
        """Create a signed JWT token with the given claims."""
        header = {
            "alg": algorithm,
            "typ": "JWT"
        }
        if kid:
            header["kid"] = kid

        current_time = int(time.time())
        default_claims = {
            "iss": "https://test-issuer.example.com",
            "sub": "test-user-123",
            "aud": "test-client-456",
            "exp": current_time + 3600,  # 1 hour from now
            "iat": current_time,
            "client_id": "test-client-456"
        }

        final_claims = {**default_claims, **claims}

        jws = JsonWebSignature()
        jwk = JsonWebKey.import_key(rsa_key_pair["private_pem"])

        payload_json = json.dumps(final_claims)

        token = jws.serialize_compact(header, payload_json, jwk)
        return token.decode('utf-8') if isinstance(token, bytes) else token

    return create_token


class TestJWTCryptographicIntegration:
    """Test JWT functions with real cryptographic operations."""

    def test_valid_token_verification(self, rsa_key_pair, jwt_token_factory):
        """Test successful verification of a properly signed JWT token."""
        claims = {
            "scope": "read write admin",
            "custom_claim": "custom_value",
            "role": "admin"
        }

        token = jwt_token_factory(claims, kid=rsa_key_pair["kid"])

        verified_claims = decode_and_verify_jwt(
            token,
            rsa_key_pair["public_pem"],
            "RS256"
        )

        assert verified_claims["iss"] == "https://test-issuer.example.com"
        assert verified_claims["sub"] == "test-user-123"
        assert verified_claims["aud"] == "test-client-456"
        assert verified_claims["client_id"] == "test-client-456"
        assert verified_claims["scope"] == "read write admin"
        assert verified_claims["custom_claim"] == "custom_value"
        assert verified_claims["role"] == "admin"

        current_time = int(time.time())
        assert verified_claims["iat"] <= current_time
        assert verified_claims["exp"] > current_time

    def test_forged_token_verification_failure(self, rsa_key_pair, jwt_token_factory):
        """Test that verification fails for a forged/tampered token."""
        claims = {"scope": "read", "role": "user"}
        token = jwt_token_factory(claims, kid=rsa_key_pair["kid"])

        header_b64, payload_b64, signature_b64 = token.split('.')

        payload_bytes = base64.urlsafe_b64decode(payload_b64 + '==')  # Add padding
        payload_data = json.loads(payload_bytes)

        payload_data["scope"] = "read write admin delete"
        payload_data["role"] = "admin"

        tampered_payload = json.dumps(payload_data)
        tampered_payload_b64 = base64.urlsafe_b64encode(
            tampered_payload.encode()
        ).decode().rstrip('=')

        forged_token = f"{header_b64}.{tampered_payload_b64}.{signature_b64}"

        with pytest.raises(ValueError, match="JWT verification failed"):
            decode_and_verify_jwt(forged_token, rsa_key_pair["public_pem"], "RS256")

    def test_wrong_key_verification_failure(self, rsa_key_pair, jwt_token_factory):
        """Test that verification fails when using the wrong public key."""
        wrong_private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        wrong_public_key = wrong_private_key.public_key()
        wrong_public_pem = wrong_public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode('utf-8')

        claims = {"scope": "read write", "role": "user"}
        token = jwt_token_factory(claims, kid=rsa_key_pair["kid"])

        with pytest.raises(ValueError, match="JWT verification failed"):
            decode_and_verify_jwt(token, wrong_public_pem, "RS256")


    def test_parse_jwt_access_token_integration(self, rsa_key_pair, jwt_token_factory):
        """Test the complete parse_jwt_access_token flow with real crypto."""
        claims = {
            "scope": "read write admin",
            "jti": "unique-token-id-123",
            "custom_role": "super_admin",
            "tenant_id": "tenant-456",
            "authorization_details": [
                {"type": "payment", "amount": "100.00", "currency": "USD"}
            ]
        }

        token = jwt_token_factory(claims, kid=rsa_key_pair["kid"])

        access_token = parse_jwt_access_token(
            token,
            rsa_key_pair["public_pem"],
            "RS256"
        )

        assert isinstance(access_token, JWTAccessToken)
        assert access_token.iss == "https://test-issuer.example.com"
        assert access_token.sub == "test-user-123"
        assert access_token.aud == "test-client-456"
        assert access_token.client_id == "test-client-456"
        assert access_token.scope == "read write admin"
        assert access_token.jti == "unique-token-id-123"

        assert access_token.get_custom_claim("custom_role") == "super_admin"
        assert access_token.get_custom_claim("tenant_id") == "tenant-456"
        assert access_token.authorization_details == [
            {"type": "payment", "amount": "100.00", "currency": "USD"}
        ]

        current_time = int(time.time())
        assert access_token.iat <= current_time
        assert access_token.exp > current_time


    def test_claims_extraction_from_real_token(self, rsa_key_pair, jwt_token_factory):
        """Test claims extraction functions with a real signed token."""
        claims = {
            "scope": "read write admin",
            "custom_data": {"nested": "value", "array": [1, 2, 3]},
            "role": "admin"
        }

        token = jwt_token_factory(claims, kid=rsa_key_pair["kid"])

        extracted_claims = get_claims(token)
        assert extracted_claims["scope"] == "read write admin"
        assert extracted_claims["custom_data"]["nested"] == "value"
        assert extracted_claims["role"] == "admin"

        extracted_header = get_header(token)
        assert extracted_header["alg"] == "RS256"
        assert extracted_header["typ"] == "JWT"
        assert extracted_header["kid"] == rsa_key_pair["kid"]

        scopes = extract_scopes(extracted_claims)
        assert scopes == ["read", "write", "admin"]
