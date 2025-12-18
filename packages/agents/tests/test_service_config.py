"""Tests for AgentServiceConfig."""

import pytest

from keycardai.agents import AgentServiceConfig
from keycardai.agents.server import SimpleExecutor


def test_service_config_basic():
    """Test basic service configuration."""
    config = AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone",
        agent_executor=SimpleExecutor(),
    )

    assert config.service_name == "Test Service"
    assert config.client_id == "test_client"
    assert config.client_secret == "test_secret"
    assert config.identity_url == "https://test.example.com"
    assert config.zone_id == "test_zone"
    assert config.port == 8000  # default
    assert config.host == "0.0.0.0"  # default


def test_service_config_urls():
    """Test URL generation."""
    config = AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone",
        agent_executor=SimpleExecutor(),
    )

    assert config.agent_card_url == "https://test.example.com/.well-known/agent-card.json"
    assert config.invoke_url == "https://test.example.com/invoke"
    assert config.status_url == "https://test.example.com/status"


def test_service_config_trailing_slash_removed():
    """Test that trailing slash is removed from identity_url."""
    config = AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com/",  # with trailing slash
        zone_id="test_zone",
        agent_executor=SimpleExecutor(),
    )

    assert config.identity_url == "https://test.example.com"
    assert not config.identity_url.endswith("/")


def test_service_config_agent_card():
    """Test agent card generation (A2A Protocol format)."""
    config = AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone",
        agent_executor=SimpleExecutor(),
        description="A test service",
        capabilities=["test1", "test2"],
    )

    card = config.to_agent_card()

    # A2A Protocol required fields (camelCase in JSON)
    assert card["name"] == "Test Service"
    assert card["description"] == "A test service"
    assert card["url"] == "https://test.example.com"
    assert card["version"] == "1.0.0"
    assert card["protocolVersion"] == "0.3.0"

    # Skills (converted from capabilities)
    assert len(card["skills"]) == 2
    assert card["skills"][0]["id"] == "test1"
    assert card["skills"][1]["id"] == "test2"

    # Capabilities metadata
    assert card["capabilities"]["streaming"] is False
    # Note: multi_turn is excluded when False by Pydantic

    # Additional interfaces (our custom invoke endpoint, camelCase)
    assert len(card["additionalInterfaces"]) == 1
    assert card["additionalInterfaces"][0]["url"] == "https://test.example.com/invoke"

    # Security (camelCase)
    assert "oauth2" in card["securitySchemes"]
    assert card["securitySchemes"]["oauth2"]["type"] == "oauth2"
    assert "test_zone.keycard.cloud" in card["securitySchemes"]["oauth2"]["flows"]["authorizationCode"]["tokenUrl"]


def test_service_config_validation_missing_fields():
    """Test validation of required fields."""
    with pytest.raises(ValueError, match="service_name is required"):
        AgentServiceConfig(
            service_name="",
            client_id="test_client",
            client_secret="test_secret",
            identity_url="https://test.example.com",
            zone_id="test_zone",
            agent_executor=SimpleExecutor(),
        )

    with pytest.raises(ValueError, match="client_id is required"):
        AgentServiceConfig(
            service_name="Test",
            client_id="",
            client_secret="test_secret",
            identity_url="https://test.example.com",
            zone_id="test_zone",
            agent_executor=SimpleExecutor(),
        )


def test_service_config_validation_invalid_url():
    """Test validation of identity_url format."""
    with pytest.raises(ValueError, match="identity_url must start with"):
        AgentServiceConfig(
            service_name="Test",
            client_id="test_client",
            client_secret="test_secret",
            identity_url="invalid-url",  # no http:// or https://
            zone_id="test_zone",
            agent_executor=SimpleExecutor(),
        )


def test_service_config_validation_invalid_port():
    """Test validation of port number."""
    with pytest.raises(ValueError, match="port must be between"):
        AgentServiceConfig(
            service_name="Test",
            client_id="test_client",
            client_secret="test_secret",
            identity_url="https://test.example.com",
            zone_id="test_zone",
            agent_executor=SimpleExecutor(),
            port=99999,  # invalid port
        )
