"""Tests for PKCE utility functions."""

import pytest

from keycardai.oauth.utils import generate_pkce_challenge, verify_pkce_challenge


class TestPKCEFunctions:
    """Test PKCE utility functions (placeholder implementations)."""

    def test_generate_pkce_challenge_not_implemented(self):
        """Test that PKCE challenge generation raises NotImplementedError."""
        with pytest.raises(
            NotImplementedError, match="PKCE generation will be implemented in Phase 2"
        ):
            generate_pkce_challenge()

    def test_verify_pkce_challenge_not_implemented(self):
        """Test that PKCE challenge verification raises NotImplementedError."""
        with pytest.raises(
            NotImplementedError,
            match="PKCE verification will be implemented in Phase 2",
        ):
            verify_pkce_challenge("verifier", "challenge")
