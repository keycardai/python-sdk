"""Smoke tests for package examples.

Verifies that each example in packages/mcp-fastmcp/examples/ imports cleanly
and exposes the expected objects. The @grant decorator validates function
signatures at import time, so a successful import confirms the example
is compatible with the current SDK API.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock, patch

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"

# Patch target: where AuthProvider.__init__ looks up DefaultClientFactory
_FACTORY_PATCH_TARGET = "keycardai.fastmcp.provider.DefaultClientFactory"


def _mock_client_factory():
    """Create a mock DefaultClientFactory that skips real HTTP calls.

    AuthProvider.__init__ calls _discover_jwks_uri which hits the zone URL.
    This mock returns fake metadata so examples can be imported without
    network access.
    """
    factory = Mock()
    mock_metadata = Mock()
    mock_metadata.jwks_uri = "https://test.keycard.cloud/.well-known/jwks.json"

    mock_client = Mock()
    mock_client.discover_server_metadata.return_value = mock_metadata

    mock_async_client = Mock()
    mock_async_client.config = Mock()
    mock_async_client.config.client_id = "test_client_id"

    factory.return_value.create_client.return_value = mock_client
    factory.return_value.create_async_client.return_value = mock_async_client
    return factory


def _load_example(example_name: str):
    """Load an example module by name from the examples directory."""
    module_path = EXAMPLES_DIR / example_name / "main.py"
    module_name = f"example_{example_name.replace('/', '_')}"

    # Remove from sys.modules if previously loaded to get a clean import
    sys.modules.pop(module_name, None)

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hello_world_example_loads():
    """The hello_world_server example imports and exposes auth_provider, auth, and mcp."""
    with patch(_FACTORY_PATCH_TARGET, _mock_client_factory()):
        mod = _load_example("hello_world_server")
    assert hasattr(mod, "auth_provider")
    assert hasattr(mod, "auth")
    assert hasattr(mod, "mcp")
    assert hasattr(mod, "main")
    assert callable(mod.main)


def test_delegated_access_example_loads():
    """The delegated_access example imports and exposes auth_provider, auth, mcp, and tool functions."""
    with patch(_FACTORY_PATCH_TARGET, _mock_client_factory()):
        mod = _load_example("delegated_access")
    assert hasattr(mod, "auth_provider")
    assert hasattr(mod, "auth")
    assert hasattr(mod, "mcp")
    # FastMCP's @mcp.tool() wraps functions in FunctionTool objects (not callable),
    # but their existence confirms @grant validated the signatures at import time.
    assert hasattr(mod, "get_github_user")
    assert hasattr(mod, "list_github_repos")
    assert hasattr(mod, "main")
    assert callable(mod.main)
