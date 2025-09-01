"""Tests for cryptographic utility functions."""

import pytest

from keycardai.oauth.utils import generate_cert_thumbprint


class TestCryptoFunctions:
    """Test cryptographic utility functions (placeholder implementations)."""

    def test_generate_cert_thumbprint_not_implemented(self):
        """Test that certificate thumbprint generation raises NotImplementedError."""
        with pytest.raises(
            NotImplementedError,
            match="Certificate thumbprint generation will be implemented in Phase 2",
        ):
            generate_cert_thumbprint(b"cert_data")
