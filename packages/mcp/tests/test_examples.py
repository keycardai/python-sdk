"""Smoke tests for package examples.

Verifies that each example in packages/mcp/examples/ imports cleanly
and exposes the expected objects. The @grant decorator validates function
signatures at import time, so a successful import confirms the example
is compatible with the current SDK API.
"""

import importlib.util
import sys
from pathlib import Path

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


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
    """The hello_world_server example imports and exposes auth_provider and mcp."""
    mod = _load_example("hello_world_server")
    assert hasattr(mod, "auth_provider")
    assert hasattr(mod, "mcp")
    assert hasattr(mod, "main")
    assert callable(mod.main)


def test_delegated_access_example_loads():
    """The delegated_access example imports and exposes auth_provider, mcp, and tool functions."""
    mod = _load_example("delegated_access")
    assert hasattr(mod, "auth_provider")
    assert hasattr(mod, "mcp")
    assert hasattr(mod, "get_github_user")
    assert hasattr(mod, "list_github_repos")
    assert callable(mod.get_github_user)
    assert callable(mod.list_github_repos)
    assert hasattr(mod, "main")
    assert callable(mod.main)
