"""Tests for AgentServiceConfig."""

import pytest

from keycardai.a2a import AgentServiceConfig


def test_service_config_basic():
    """Required fields hold the values passed in."""
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
    assert config.description == ""
    assert config.capabilities == []


def test_service_config_urls():
    """URL helpers compose against identity_url and zone_id."""
    config = AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone",
    )

    assert config.agent_card_url == "https://test.example.com/.well-known/agent-card.json"
    assert config.jsonrpc_url == "https://test.example.com/a2a/jsonrpc"
    assert config.auth_server_url == "https://test_zone.keycard.cloud"


def test_service_config_authorization_server_override():
    """An explicit authorization_server_url overrides the zone-derived default."""
    config = AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone",
        authorization_server_url="https://custom.example.com",
    )

    assert config.auth_server_url == "https://custom.example.com"


def test_service_config_trailing_slash_removed():
    """A trailing slash on identity_url is stripped."""
    config = AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com/",
        zone_id="test_zone",
    )

    assert config.identity_url == "https://test.example.com"
    assert not config.identity_url.endswith("/")


def test_service_config_validation_missing_fields():
    """Empty required fields raise on construction."""
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
    """identity_url must start with http:// or https://."""
    with pytest.raises(ValueError, match="identity_url must start with"):
        AgentServiceConfig(
            service_name="Test",
            client_id="test_client",
            client_secret="test_secret",
            identity_url="invalid-url",
            zone_id="test_zone",
        )
