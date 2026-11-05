"""The pydantic_agents adapter warns on use, not on import, and runs here
without pydantic_ai installed: the framework import is stubbed."""

from __future__ import annotations

import importlib
import sys
import types
import warnings
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def pydantic_agents(monkeypatch: pytest.MonkeyPatch):
    if "pydantic_ai" not in sys.modules:
        stub = types.ModuleType("pydantic_ai")
        stub.Tool = MagicMock()  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pydantic_ai", stub)
    module = importlib.import_module(
        "keycardai.mcp.client.integrations.pydantic_agents"
    )
    return importlib.reload(module)


def test_importing_the_integrations_package_does_not_warn() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        import keycardai.mcp.client.integrations as integrations

        importlib.reload(integrations)


def test_importing_the_module_does_not_warn(pydantic_agents) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        importlib.reload(pydantic_agents)


def test_constructing_the_client_warns_at_the_caller(pydantic_agents) -> None:
    with pytest.warns(DeprecationWarning, match="keycardai-pydantic-ai") as record:
        pydantic_agents.PydanticAIClient(MagicMock())
    assert record[0].filename == __file__
    assert "next major release of keycardai-mcp" in str(record[0].message)


def test_create_client_warns_at_the_caller(pydantic_agents) -> None:
    with pytest.warns(DeprecationWarning, match="keycardai-pydantic-ai") as record:
        pydantic_agents.create_client(MagicMock())
    assert any(w.filename == __file__ for w in record)
