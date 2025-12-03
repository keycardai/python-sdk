"""Basic smoke test for keycardai-agents package."""

import pytest

# Note: These tests require crewai to be installed
# Run with: uv run --with crewai pytest tests/

pytest.importorskip("crewai")


def test_import_package() -> None:
    """Test that the package can be imported."""
    from keycardai.agents import CrewAIClient, create_client

    assert CrewAIClient is not None
    assert create_client is not None


def test_import_crewai_agents() -> None:
    """Test that crewai_agents module can be imported."""
    from keycardai.agents import crewai_agents

    assert crewai_agents is not None
    assert hasattr(crewai_agents, "CrewAIClient")
    assert hasattr(crewai_agents, "create_client")
    assert hasattr(crewai_agents, "AuthToolHandler")


def test_package_has_all() -> None:
    """Test that __all__ is properly defined."""
    import keycardai.agents

    assert hasattr(keycardai.agents, "__all__")
    assert "CrewAIClient" in keycardai.agents.__all__
    assert "create_client" in keycardai.agents.__all__
