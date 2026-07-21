"""Tests for the mock_access_context testing utility.

mock_access_context installs a preloaded AccessContext through the public
override_access_context seam; nothing here patches module internals, and
tools exercise the same grant resolution code paths as production.
"""

import pytest
from fastmcp.server.dependencies import without_injected_parameters

from keycardai.fastmcp import (
    AccessContext,
    AuthProvider,
    ResourceAccessError,
    mock_access_context,
)


@pytest.fixture
def auth_provider(auth_provider_config, mock_client_factory):
    return AuthProvider(**auth_provider_config, client_factory=mock_client_factory)


@pytest.mark.asyncio
async def test_default_token_for_any_resource(auth_provider):
    async def tool(
        access: AccessContext = auth_provider.grant("https://api.example.com"),
    ) -> str:
        assert not access.has_errors()
        return access.access("https://api.example.com").access_token

    wrapper = without_injected_parameters(tool)
    with mock_access_context():
        assert await wrapper() == "test_access_token"
    with mock_access_context(access_token="my_token"):
        assert await wrapper() == "my_token"


@pytest.mark.asyncio
async def test_resource_specific_tokens(auth_provider):
    async def tool(
        access: AccessContext = auth_provider.grant(
            ["https://api.example.com", "https://api.other.com"]
        ),
    ) -> tuple[str, str]:
        return (
            access.access("https://api.example.com").access_token,
            access.access("https://api.other.com").access_token,
        )

    wrapper = without_injected_parameters(tool)
    with mock_access_context(resource_tokens={
        "https://api.example.com": "token_123",
        "https://api.other.com": "token_456",
    }):
        assert await wrapper() == ("token_123", "token_456")


def test_resource_not_granted_raises_and_records():
    with mock_access_context(resource_tokens={
        "https://api.example.com": "token_123",
    }) as access:
        with pytest.raises(ResourceAccessError):
            access.access("https://api.other.com")
        assert access.has_errors()
        errors = access.get_errors()
        assert "https://api.other.com" in errors["resources"]


def test_error_state():
    with mock_access_context(has_errors=True, error_message="Auth failed") as access:
        assert access.has_errors()
        assert access.get_errors()["error"] == {"message": "Auth failed"}
        with pytest.raises(Exception, match=""):
            access.access("https://api.example.com")


def test_yields_real_access_context():
    with mock_access_context() as access:
        assert isinstance(access, AccessContext)


@pytest.mark.asyncio
async def test_works_with_decorator_path(auth_provider):
    @auth_provider.grant("https://api.example.com")
    async def tool(access: AccessContext = None) -> str:
        return access.access("https://api.example.com").access_token

    with mock_access_context(access_token="decorator_token"):
        assert await tool() == "decorator_token"
