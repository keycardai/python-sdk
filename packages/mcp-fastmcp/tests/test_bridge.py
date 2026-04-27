"""Bridge contract tests for keycardai-mcp-fastmcp.

The implementation moved to ``keycardai-fastmcp``. This test file asserts the
deprecation bridge keeps the old import path working: importing from
``keycardai.mcp.integrations.fastmcp.*`` emits a ``DeprecationWarning`` and
returns the same objects exposed by ``keycardai.fastmcp.*``.

Behavioral tests for the FastMCP integration itself live in the keycardai-fastmcp
test suite.
"""

import importlib
import warnings


def test_top_level_import_emits_deprecation_warning():
    """Importing keycardai.mcp.integrations.fastmcp warns about the rename."""
    import sys

    sys.modules.pop("keycardai.mcp.integrations.fastmcp", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("keycardai.mcp.integrations.fastmcp")

    matches = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "keycardai.fastmcp" in str(w.message)
    ]
    assert len(matches) >= 1, f"Expected DeprecationWarning, got {caught}"


def test_bridge_returns_canonical_classes():
    """The bridge re-exports return the same objects as the canonical module."""
    import keycardai.fastmcp as canonical

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import keycardai.mcp.integrations.fastmcp as bridge

    assert bridge.AuthProvider is canonical.AuthProvider
    assert bridge.AccessContext is canonical.AccessContext
    assert bridge.ClientSecret is canonical.ClientSecret
    assert bridge.WebIdentity is canonical.WebIdentity
    assert bridge.mock_access_context is canonical.mock_access_context


def test_bridge_provider_submodule_exposes_canonical_symbols():
    """``from keycardai.mcp.integrations.fastmcp.provider import X`` keeps working."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from keycardai.fastmcp.provider import AuthProvider as canonical_provider
        from keycardai.mcp.integrations.fastmcp.provider import (
            AuthProvider as bridge_provider,
        )

    assert bridge_provider is canonical_provider


def test_bridge_testing_submodule_exposes_canonical_symbols():
    """``keycardai.mcp.integrations.fastmcp.testing.mock_access_context`` keeps working."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from keycardai.fastmcp.testing import mock_access_context as canonical
        from keycardai.mcp.integrations.fastmcp.testing import (
            mock_access_context as bridge,
        )

    assert bridge is canonical
