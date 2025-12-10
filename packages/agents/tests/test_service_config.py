"""Tests for AgentServiceConfig."""

import pytest
from keycardai.agents import AgentServiceConfig


def test_service_config_basic():
    """Test basic service configuration."""
    config = AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone",
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
    )

    assert config.identity_url == "https://test.example.com"
    assert not config.identity_url.endswith("/")


def test_service_config_agent_card():
    """Test agent card generation."""
    config = AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone",
        description="A test service",
        capabilities=["test1", "test2"],
    )

    card = config.to_agent_card()

    assert card["name"] == "Test Service"
    assert card["description"] == "A test service"
    assert card["type"] == "crew_service"
    assert card["identity"] == "https://test.example.com"
    assert card["capabilities"] == ["test1", "test2"]
    assert card["endpoints"]["invoke"] == "https://test.example.com/invoke"
    assert card["endpoints"]["status"] == "https://test.example.com/status"
    assert card["auth"]["type"] == "oauth2"
    assert "test_zone.keycard.cloud" in card["auth"]["token_url"]


def test_service_config_validation_missing_fields():
    """Test validation of required fields."""
    with pytest.raises(ValueError, match="service_name is required"):
        AgentServiceConfig(
            service_name="",
            client_id="test_client",
            client_secret="test_secret",
            identity_url="https://test.example.com",
            zone_id="test_zone",
        )

    with pytest.raises(ValueError, match="client_id is required"):
        AgentServiceConfig(
            service_name="Test",
            client_id="",
            client_secret="test_secret",
            identity_url="https://test.example.com",
            zone_id="test_zone",
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
            port=99999,  # invalid port
        )
