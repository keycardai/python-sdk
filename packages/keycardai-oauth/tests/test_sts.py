"""Basic tests for the OAuth SDK."""

from keycardai.oauth import __version__


def test_version():
    """Test that version is accessible."""
    assert __version__ == "0.0.1"


def test_import():
    """Test that the package can be imported."""
    import keycardai.oauth  # noqa: F401
