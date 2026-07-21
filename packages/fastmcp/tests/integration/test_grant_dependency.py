"""Integration tests for grant-as-dependency typed injection.

Covers the GrantDependency returned by AuthProvider.grant():
- injected-parameter path resolved through FastMCP's dependency machinery
- decorator path (still supported from the same object), including the
  decoration-time DeprecationWarning and dual-write to context state
- error-capture contract (exchange failures recorded, never raised)
- AccessContext.from_context() escape hatch
- override_access_context() public testing seam
"""

import inspect
import warnings
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastmcp import Context
from fastmcp.dependencies import Dependency
from fastmcp.server.dependencies import (
    AccessToken,
    without_injected_parameters,
)

from keycardai.fastmcp import (
    AccessContext,
    AuthProvider,
    GrantDependency,
)
from keycardai.fastmcp.testing import override_access_context
from keycardai.mcp.server.exceptions import MissingContextError
from keycardai.oauth.types.models import TokenResponse


def create_mock_context():
    """Create a mock Context with real state management."""
    mock_context = Mock(spec=Context)
    context_state = {}

    async def mock_set_state(key: str, value, *, serializable=True):
        context_state[key] = value

    async def mock_get_state(key: str):
        return context_state.get(key)

    mock_context.set_state = mock_set_state
    mock_context.get_state = mock_get_state
    return mock_context


@pytest.fixture
def auth_provider(auth_provider_config, mock_client_factory):
    return AuthProvider(**auth_provider_config, client_factory=mock_client_factory)


class TestGrantDependencyInjection:
    """Injected-parameter path: access: AccessContext = auth_provider.grant(...)."""

    def test_grant_returns_fastmcp_dependency(self, auth_provider):
        dep = auth_provider.grant("https://api.example.com")
        assert isinstance(dep, GrantDependency)
        assert isinstance(dep, Dependency)

    @pytest.mark.asyncio
    @patch("keycardai.fastmcp.provider.get_access_token")
    async def test_injected_param_happy_path(self, mock_get_token, auth_provider):
        """FastMCP's dependency machinery injects a populated AccessContext."""
        mock_get_token.return_value = AccessToken(
            token="test_token", client_id="test_client", scopes=["test_scope"]
        )

        async def get_data(
            user_id: str,
            access: AccessContext = auth_provider.grant("https://api.example.com"),
        ) -> str:
            assert not access.has_errors()
            token = access.access("https://api.example.com").access_token
            return f"{user_id}:{token}"

        # Resolve through FastMCP's own dependency machinery.
        wrapper = without_injected_parameters(get_data)
        result = await wrapper(user_id="user123")
        assert result == "user123:exchanged_token_123"

    def test_injected_param_hidden_from_signature(self, auth_provider):
        """The AccessContext parameter is excluded from the tool's public signature."""

        async def get_data(
            user_id: str,
            access: AccessContext = auth_provider.grant("https://api.example.com"),
        ) -> str:
            return user_id

        wrapper = without_injected_parameters(get_data)
        assert list(inspect.signature(wrapper).parameters.keys()) == ["user_id"]

    @pytest.mark.asyncio
    @patch("keycardai.fastmcp.provider.get_access_token")
    async def test_injected_param_multiple_resources(self, mock_get_token, auth_provider):
        mock_get_token.return_value = AccessToken(
            token="test_token", client_id="test_client", scopes=["test_scope"]
        )
        dep = auth_provider.grant(
            ["https://api1.example.com", "https://api2.example.com"]
        )

        async with dep as access:
            assert access.access("https://api1.example.com").access_token == "token_api1_123"
            assert access.access("https://api2.example.com").access_token == "token_api2_456"

    @pytest.mark.asyncio
    @patch("keycardai.fastmcp.provider.get_access_token")
    async def test_exchange_failure_recorded_not_raised(self, mock_get_token, auth_provider):
        """Error-capture contract: __aenter__ never raises on exchange failure."""
        mock_get_token.return_value = AccessToken(
            token="test_token", client_id="test_client", scopes=["test_scope"]
        )
        failing_client = AsyncMock()
        failing_client.exchange_token.side_effect = Exception("Exchange failed")
        auth_provider.client = failing_client

        dep = auth_provider.grant("https://api.example.com")
        async with dep as access:
            assert access.has_errors()
            assert access.has_resource_error("https://api.example.com")
            error = access.get_resource_error("https://api.example.com")
            assert error["message"] == "Token exchange failed for https://api.example.com"
            assert error["raw_error"] == "Exchange failed"

    @pytest.mark.asyncio
    @patch("keycardai.fastmcp.provider.get_access_token")
    async def test_missing_caller_token_recorded_not_raised(self, mock_get_token, auth_provider):
        """Error-capture contract: missing caller token becomes a global error."""
        mock_get_token.return_value = None

        dep = auth_provider.grant("https://api.example.com")
        async with dep as access:
            assert access.has_error()
            assert "No authentication token available" in access.get_error()["message"]

    @pytest.mark.asyncio
    @patch("keycardai.fastmcp.provider.get_access_token")
    async def test_request_scopes_forwarded(self, mock_get_token, auth_provider):
        mock_get_token.return_value = AccessToken(
            token="test_token", client_id="test_client", scopes=["test_scope"]
        )
        captured = {}

        async def capturing_exchange(request):
            captured[request.resource] = request
            return TokenResponse(access_token="exchanged", token_type="Bearer")

        capturing_client = AsyncMock()
        capturing_client.exchange_token.side_effect = capturing_exchange
        auth_provider.client = capturing_client

        dep = auth_provider.grant("https://api.example.com", request_scopes="read")
        async with dep as access:
            assert not access.has_errors()
        assert captured["https://api.example.com"].scope == "read"


class TestGrantDecoratorPath:
    """Decorator spelling still works from the same GrantDependency object."""

    @pytest.mark.asyncio
    @patch("keycardai.fastmcp.provider.get_access_token")
    async def test_decorator_still_works_and_warns_once(self, mock_get_token, auth_provider):
        mock_get_token.return_value = AccessToken(
            token="test_token", client_id="test_client", scopes=["test_scope"]
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            @auth_provider.grant("https://api.example.com")
            async def legacy_tool(ctx: Context, user_id: str):
                access = await ctx.get_state("keycardai")
                return access.access("https://api.example.com").access_token

        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecations) == 1, "decoration emits exactly one DeprecationWarning per tool"
        assert "legacy_tool" in str(deprecations[0].message)
        assert "AccessContext" in str(deprecations[0].message)

        result = await legacy_tool(create_mock_context(), "user123")
        assert result == "exchanged_token_123"

    @pytest.mark.asyncio
    @patch("keycardai.fastmcp.provider.get_access_token")
    async def test_decorator_with_access_param_no_warning(self, mock_get_token, auth_provider):
        """Declaring an AccessContext parameter silences the deprecation warning."""
        mock_get_token.return_value = AccessToken(
            token="test_token", client_id="test_client", scopes=["test_scope"]
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            @auth_provider.grant("https://api.example.com")
            async def typed_tool(user_id: str, access: AccessContext = None):
                return access.access("https://api.example.com").access_token

        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert deprecations == []

        # The injected parameter is hidden from the wrapper's public signature.
        assert list(inspect.signature(typed_tool).parameters.keys()) == ["user_id"]

        result = await typed_tool("user123")
        assert result == "exchanged_token_123"

    @pytest.mark.asyncio
    @patch("keycardai.fastmcp.provider.get_access_token")
    async def test_decorator_dual_writes_context_state(self, mock_get_token, auth_provider):
        """Decorator keeps writing set_state("keycardai") during deprecation."""
        mock_get_token.return_value = AccessToken(
            token="test_token", client_id="test_client", scopes=["test_scope"]
        )

        @auth_provider.grant("https://api.example.com")
        async def legacy_tool(ctx: Context):
            return "ok"

        mock_context = create_mock_context()
        await legacy_tool(mock_context)

        state = await mock_context.get_state("keycardai")
        assert isinstance(state, AccessContext)
        assert state.access("https://api.example.com").access_token == "exchanged_token_123"

    def test_decorator_without_context_or_access_param_raises(self, auth_provider):
        with pytest.raises(MissingContextError):

            @auth_provider.grant("https://api.example.com")
            def no_injection(user_id: str) -> str:
                return user_id

    def test_decorator_handles_string_and_union_annotations(self, auth_provider):
        """Context detection works with string annotations and Context | None."""

        @auth_provider.grant("https://api.example.com")
        async def string_annotated(ctx: "Context"):
            return "ok"

        @auth_provider.grant("https://api.example.com")
        async def union_annotated(ctx: Context | None = None):
            return "ok"

        # Decoration succeeded for both; MissingContextError was not raised.
        assert callable(string_annotated)
        assert callable(union_annotated)


class TestFromContext:
    """AccessContext.from_context() escape hatch for helpers inside tools."""

    @pytest.mark.asyncio
    @patch("keycardai.fastmcp.provider.get_access_token")
    async def test_from_context_returns_stored_access_context(self, mock_get_token, auth_provider):
        mock_get_token.return_value = AccessToken(
            token="test_token", client_id="test_client", scopes=["test_scope"]
        )

        async def helper(ctx: Context) -> str:
            access = await AccessContext.from_context(ctx)
            return access.access("https://api.example.com").access_token

        @auth_provider.grant("https://api.example.com")
        async def tool(ctx: Context):
            return await helper(ctx)

        result = await tool(create_mock_context())
        assert result == "exchanged_token_123"

    @pytest.mark.asyncio
    async def test_from_context_without_grant_records_error(self):
        access = await AccessContext.from_context(create_mock_context())
        assert isinstance(access, AccessContext)
        assert access.has_error()
        assert "No Keycard access context" in access.get_error()["message"]


class TestEndToEndWithFastMCPServer:
    """Full round trip through a real FastMCP server and in-memory client."""

    @pytest.mark.asyncio
    async def test_injected_param_through_real_server(self, auth_provider):
        from fastmcp import Client, FastMCP

        mcp = FastMCP("test-server")

        @mcp.tool()
        async def get_data(
            user_id: str,
            access: AccessContext = auth_provider.grant("https://api.example.com"),
        ) -> str:
            token = access.access("https://api.example.com").access_token
            return f"{user_id}:{token}"

        fake = AccessContext()
        fake.set_token(
            "https://api.example.com",
            TokenResponse(access_token="e2e_token", token_type="Bearer"),
        )

        with override_access_context(fake):
            async with Client(mcp) as client:
                tools = await client.list_tools()
                schema_props = tools[0].inputSchema.get("properties", {})
                assert list(schema_props.keys()) == ["user_id"], (
                    "AccessContext parameter must not appear in the tool input schema"
                )

                result = await client.call_tool("get_data", {"user_id": "user123"})
                assert result.content[0].text == "user123:e2e_token"


class TestOverrideAccessContextSeam:
    """override_access_context() bypasses token acquisition and exchange."""

    @pytest.mark.asyncio
    async def test_override_short_circuits_dependency_resolution(self, auth_provider):
        # No get_access_token patching and no client mocking: the override
        # must short-circuit before either is touched.
        auth_provider.client = None

        fake = AccessContext()
        fake.set_token(
            "https://api.example.com",
            TokenResponse(access_token="seam_token", token_type="Bearer"),
        )

        async def get_data(
            access: AccessContext = auth_provider.grant("https://api.example.com"),
        ) -> str:
            return access.access("https://api.example.com").access_token

        wrapper = without_injected_parameters(get_data)
        with override_access_context(fake):
            assert await wrapper() == "seam_token"

    @pytest.mark.asyncio
    async def test_override_applies_to_decorator_path(self, auth_provider):
        auth_provider.client = None

        fake = AccessContext()
        fake.set_token(
            "https://api.example.com",
            TokenResponse(access_token="seam_token", token_type="Bearer"),
        )

        @auth_provider.grant("https://api.example.com")
        async def legacy_tool(ctx: Context):
            access = await ctx.get_state("keycardai")
            return access.access("https://api.example.com").access_token

        with override_access_context(fake):
            assert await legacy_tool(create_mock_context()) == "seam_token"

    @pytest.mark.asyncio
    async def test_override_resets_after_exit(self, auth_provider):
        fake = AccessContext()
        with override_access_context(fake):
            pass

        with patch("keycardai.fastmcp.provider.get_access_token", return_value=None):
            dep = auth_provider.grant("https://api.example.com")
            async with dep as access:
                assert access is not fake
