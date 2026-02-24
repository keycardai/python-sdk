"""Smoke tests for package examples.

Verifies that each example in packages/oauth/examples/ imports cleanly
and exposes the expected objects. A successful import confirms the example
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


def test_discover_server_metadata_example_loads():
    """The discover_server_metadata example imports and exposes a main function."""
    mod = _load_example("discover_server_metadata")
    assert hasattr(mod, "main")
    assert callable(mod.main)


def test_dynamic_client_registration_example_loads():
    """The dynamic_client_registration example imports and exposes a main function."""
    mod = _load_example("dynamic_client_registration")
    assert hasattr(mod, "main")
    assert callable(mod.main)
