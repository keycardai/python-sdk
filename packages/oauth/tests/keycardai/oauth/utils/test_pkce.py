"""Tests for PKCE utility functions (RFC 7636)."""

import base64
import hashlib

import pytest

from keycardai.oauth.utils.pkce import PKCEGenerator


class TestGenerateCodeVerifier:
    """Test code verifier generation per RFC 7636 Section 4.1."""

    def test_default_length(self):
        verifier = PKCEGenerator.generate_code_verifier()
        assert len(verifier) == 128

    def test_minimum_length(self):
        verifier = PKCEGenerator.generate_code_verifier(43)
        assert len(verifier) == 43

    def test_maximum_length(self):
        verifier = PKCEGenerator.generate_code_verifier(128)
        assert len(verifier) == 128

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="between 43 and 128"):
            PKCEGenerator.generate_code_verifier(42)

    def test_too_long_raises(self):
        with pytest.raises(ValueError, match="between 43 and 128"):
            PKCEGenerator.generate_code_verifier(129)

    def test_uses_unreserved_characters_only(self):
        """RFC 7636 Section 4.1: verifier uses [A-Z] [a-z] [0-9] "-" "." "_" "~"."""
        verifier = PKCEGenerator.generate_code_verifier()
        # token_urlsafe produces [A-Za-z0-9_-], which is a subset of the allowed set.
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert set(verifier).issubset(allowed)

    def test_unique_across_calls(self):
        v1 = PKCEGenerator.generate_code_verifier()
        v2 = PKCEGenerator.generate_code_verifier()
        assert v1 != v2


class TestGenerateCodeChallenge:
    """Test code challenge generation per RFC 7636 Section 4.2."""

    def test_s256_rfc_appendix_b(self):
        """Verify S256 against the test vector from RFC 7636 Appendix B.

        Reference: https://datatracker.ietf.org/doc/html/rfc7636#appendix-B
        """
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        expected_challenge = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        challenge = PKCEGenerator.generate_code_challenge(verifier, "S256")
        assert challenge == expected_challenge

    def test_s256_manual_computation(self):
        verifier = "test-verifier-string"
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        assert PKCEGenerator.generate_code_challenge(verifier, "S256") == expected

    def test_plain_returns_verifier(self):
        verifier = "some-code-verifier"
        challenge = PKCEGenerator.generate_code_challenge(verifier, "plain")
        assert challenge == verifier

    def test_unsupported_method_raises(self):
        with pytest.raises(ValueError, match="Unsupported PKCE method"):
            PKCEGenerator.generate_code_challenge("verifier", "RS256")


class TestGeneratePKCEPair:
    """Test PKCE pair generation."""

    def test_s256_pair(self):
        gen = PKCEGenerator()
        pair = gen.generate_pkce_pair(method="S256", verifier_length=64)
        assert len(pair.code_verifier) == 64
        assert pair.code_challenge_method == "S256"
        # Challenge should be a valid base64url string, not the verifier itself
        assert pair.code_challenge != pair.code_verifier

    def test_plain_pair(self):
        gen = PKCEGenerator()
        pair = gen.generate_pkce_pair(method="plain", verifier_length=43)
        assert pair.code_challenge == pair.code_verifier
        assert pair.code_challenge_method == "plain"

    def test_default_method_is_s256(self):
        gen = PKCEGenerator()
        pair = gen.generate_pkce_pair()
        assert pair.code_challenge_method == "S256"


class TestValidatePKCEPair:
    """Test PKCE pair validation per RFC 7636 Section 4.6."""

    def test_s256_round_trip(self):
        gen = PKCEGenerator()
        pair = gen.generate_pkce_pair(method="S256")
        assert gen.validate_pkce_pair(pair.code_verifier, pair.code_challenge, "S256")

    def test_plain_round_trip(self):
        gen = PKCEGenerator()
        pair = gen.generate_pkce_pair(method="plain")
        assert gen.validate_pkce_pair(pair.code_verifier, pair.code_challenge, "plain")

    def test_wrong_verifier_fails(self):
        gen = PKCEGenerator()
        pair = gen.generate_pkce_pair(method="S256")
        assert not gen.validate_pkce_pair("wrong-verifier-value-padded-to-be-long-enough", pair.code_challenge, "S256")

    def test_wrong_challenge_fails(self):
        gen = PKCEGenerator()
        pair = gen.generate_pkce_pair(method="S256")
        assert not gen.validate_pkce_pair(pair.code_verifier, "wrong-challenge", "S256")
