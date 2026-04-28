"""Tests for AgentServiceConfig."""

import pytest

from keycardai.a2a import AgentServiceConfig
from tests._helpers import NoopAgentExecutor


def test_service_config_basic():
    """Test basic service configuration."""
    config = AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone",
        agent_executor=NoopAgentExecutor(),
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
        agent_executor=NoopAgentExecutor(),
    )

    assert config.agent_card_url == "https://test.example.com/.well-known/agent-card.json"
    assert config.jsonrpc_url == "https://test.example.com/a2a/jsonrpc"
    assert config.status_url == "https://test.example.com/status"


def test_service_config_trailing_slash_removed():
    """Test that trailing slash is removed from identity_url."""
    config = AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com/",  # with trailing slash
        zone_id="test_zone",
        agent_executor=NoopAgentExecutor(),
    )

    assert config.identity_url == "https://test.example.com"
    assert not config.identity_url.endswith("/")


def test_service_config_validation_missing_fields():
    """Test validation of required fields."""
    with pytest.raises(ValueError, match="service_name is required"):
        AgentServiceConfig(
            service_name="",
            client_id="test_client",
            client_secret="test_secret",
            identity_url="https://test.example.com",
            zone_id="test_zone",
            agent_executor=NoopAgentExecutor(),
        )

    with pytest.raises(ValueError, match="client_id is required"):
        AgentServiceConfig(
            service_name="Test",
            client_id="",
            client_secret="test_secret",
            identity_url="https://test.example.com",
            zone_id="test_zone",
            agent_executor=NoopAgentExecutor(),
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
            agent_executor=NoopAgentExecutor(),
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
            agent_executor=NoopAgentExecutor(),
            port=99999,  # invalid port
        )
