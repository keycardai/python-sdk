"""Tests for JWT utility functions."""

import pytest

from keycardai.oauth.utils import create_jwt_assertion


class TestJWTFunctions:
    """Test JWT utility functions (placeholder implementations)."""

    def test_create_jwt_assertion_not_implemented(self):
        """Test that JWT assertion creation raises NotImplementedError."""
        with pytest.raises(
            NotImplementedError,
            match="JWT assertion creation will be implemented in Phase 2",
        ):
            create_jwt_assertion("client_id", "audience", "private_key")
