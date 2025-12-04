"""Basic smoke test for CrewAI integration."""

import pytest

# Note: These tests require crewai to be installed
# Run with: uv run --with crewai pytest tests/

pytest.importorskip("crewai")


def test_import_crewai_integration() -> None:
    """Test that the CrewAI integration can be imported."""
    from keycardai.mcp.client.integrations import crewai_agents

    assert crewai_agents is not None
    assert hasattr(crewai_agents, "CrewAIClient")
    assert hasattr(crewai_agents, "create_client")
    assert hasattr(crewai_agents, "AuthToolHandler")


def test_import_crewai_client() -> None:
    """Test that CrewAIClient can be imported directly."""
    from keycardai.mcp.client.integrations.crewai_agents import (
        CrewAIClient,
        create_client,
    )

    assert CrewAIClient is not None
    assert create_client is not None


def test_crewai_in_integrations_all() -> None:
    """Test that crewai_agents is exported in integrations __all__."""
    from keycardai.mcp.client import integrations

    assert hasattr(integrations, "__all__")
    assert "crewai_agents" in integrations.__all__
