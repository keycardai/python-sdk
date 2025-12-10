"""Pytest configuration for agents tests."""

import pytest


@pytest.fixture
def mock_zone_id():
    """Mock Keycard zone ID."""
    return "test_zone_123"


@pytest.fixture
def mock_service_url():
    """Mock service URL."""
    return "https://test.example.com"
