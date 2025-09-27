"""Unit tests for the grant decorator in provider.py.

This module tests the grant decorator's parameter handling, signature validation,
and context injection behavior for various function signatures and call patterns.
"""

import inspect
from typing import Any
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import Context
from mcp.shared.context import RequestContext

from keycardai.mcp.server.auth import (
    AccessContext,
    AuthProvider,
    MissingAccessContextError,
    MissingContextError,
)


class TestGrantDecoratorSignatureValidation:
    """Test grant decorator signature validation and parameter requirements."""

    def test_decorator_rejects_function_without_context(self, auth_provider_config, mock_client_factory):
        """Test that decorator raises MissingContextError when function has no Context parameter."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        with pytest.raises(MissingContextError):
            @auth_provider.grant("https://api.example.com")
            def function_without_context(access_ctx: AccessContext, user_id: str) -> str:
                return f"Hello {user_id}"

    def test_decorator_rejects_function_without_access_context(self, auth_provider_config, mock_client_factory):
        """Test that decorator raises MissingAccessContextError when function has no AccessContext parameter."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        with pytest.raises(MissingAccessContextError):
            @auth_provider.grant("https://api.example.com")
            def function_without_access_context(ctx: Context, user_id: str) -> str:
                return f"Hello {user_id}"

    def test_decorator_accepts_function_with_request_context(self, auth_provider_config, mock_client_factory):
        """Test that decorator accepts RequestContext as alternative to Context."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        # Should not raise an exception
        @auth_provider.grant("https://api.example.com")
        def function_with_request_context(access_ctx: AccessContext, ctx: RequestContext, user_id: str) -> str:
            return f"Hello {user_id}"

        # Verify the function was decorated successfully
        assert hasattr(function_with_request_context, '__wrapped__')

    def test_decorator_accepts_valid_function_signature(self, auth_provider_config, mock_client_factory):
        """Test that decorator accepts function with both Context and AccessContext parameters."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        # Should not raise an exception
        @auth_provider.grant("https://api.example.com")
        def valid_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> str:
            return f"Hello {user_id}"

        # Verify the function was decorated successfully
        assert hasattr(valid_function, '__wrapped__')


class TestGrantDecoratorParameterHandling:
    """Test grant decorator parameter handling for different call patterns."""

    def create_mock_context_with_auth(self):
        """Helper to create a mock Context with authentication info."""
        mock_context = Mock(spec=Context)
        mock_context.request_context = Mock()
        mock_context.request_context.request = Mock()
        mock_context.request_context.request.state.keycardai_auth_info = {
            "access_token": "test_token",
            "zone_id": "test123",
            "resource_client_id": "https://api.example.com",
            "resource_server_url": "https://api.example.com"
        }
        return mock_context

    def create_mock_context_without_auth(self):
        """Helper to create a mock Context without authentication info."""
        mock_context = Mock(spec=Context)
        mock_context.request_context = Mock()
        mock_context.request_context.request = Mock()
        mock_context.request_context.request.state = {}
        return mock_context

    @pytest.mark.asyncio
    async def test_function_called_without_context_value(self, auth_provider_config, mock_client_factory):
        """Test function called without providing Context value."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"]}
            return {"success": True}

        # Call without providing ctx parameter - should cause TypeError due to missing required argument
        with pytest.raises(TypeError, match="missing 1 required positional argument: 'ctx'"):
            await test_function(user_id="test_user")

    @pytest.mark.asyncio
    async def test_function_called_with_context_as_none(self, auth_provider_config, mock_client_factory):
        """Test function called with Context value provided as None."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"]}
            return {"success": True}

        # Call with ctx=None - should cause error
        result = await test_function(ctx=None, user_id="test_user")

        assert "error" in result

    @pytest.mark.asyncio
    async def test_function_called_with_context_via_positional_args(self, auth_provider_config, mock_client_factory):
        """Test function called with Context value provided via positional arguments."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"]}
            return {"success": True, "user_id": user_id}

        mock_context = self.create_mock_context_with_auth()

        # Call with positional arguments: access_ctx, ctx, user_id
        result = await test_function(AccessContext(), mock_context, "test_user")

        # Should work correctly now
        assert result["success"] is True
        assert result["user_id"] == "test_user"

    @pytest.mark.asyncio
    async def test_function_called_with_context_via_kwargs(self, auth_provider_config, mock_client_factory):
        """Test function called with Context value provided via named arguments."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"]}
            return {"success": True, "user_id": user_id}

        mock_context = self.create_mock_context_with_auth()

        # Call with named arguments
        result = await test_function(ctx=mock_context, user_id="test_user")

        # Should work correctly now
        assert result["success"] is True
        assert result["user_id"] == "test_user"

    @pytest.mark.asyncio
    async def test_function_called_without_access_context_value(self, auth_provider_config, mock_client_factory):
        """Test function called without AccessContext value - should be auto-injected."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            # AccessContext should be auto-injected even if not provided
            assert isinstance(access_ctx, AccessContext)
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"]}
            return {"success": True, "has_access_ctx": True}

        mock_context = self.create_mock_context_with_auth()

        # Call without providing access_ctx - should be auto-injected
        result = await test_function(ctx=mock_context, user_id="test_user")

        # Should work correctly now
        assert result["success"] is True
        assert result["has_access_ctx"] is True

    @pytest.mark.asyncio
    async def test_function_called_with_access_context_via_positional_args(self, auth_provider_config, mock_client_factory):
        """Test function called with AccessContext provided as positional argument."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"]}
            return {"success": True, "user_id": user_id}

        mock_context = self.create_mock_context_with_auth()
        custom_access_ctx = AccessContext()

        # Call with AccessContext as positional argument
        result = await test_function(custom_access_ctx, mock_context, "test_user")

        # Should work correctly now
        assert result["success"] is True
        assert result["user_id"] == "test_user"

    @pytest.mark.asyncio
    async def test_function_called_with_access_context_via_kwargs(self, auth_provider_config, mock_client_factory):
        """Test function called with AccessContext provided as named argument."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"]}
            return {"success": True, "user_id": user_id}

        mock_context = self.create_mock_context_with_auth()
        custom_access_ctx = AccessContext()

        # Should work correctly now
        result = await test_function(access_ctx=custom_access_ctx, ctx=mock_context, user_id="test_user")
        assert result["success"] is True
        assert result["user_id"] == "test_user"


class TestGrantDecoratorContextExtraction:
    """Test grant decorator's context extraction and authentication info handling."""

    def create_mock_request_context_with_auth(self):
        """Helper to create a mock RequestContext with authentication info."""
        mock_request_context = Mock(spec=RequestContext)
        mock_request_context.request = Mock()
        mock_request_context.request.state.keycardai_auth_info = {
            "access_token": "test_token",
            "zone_id": "test123",
            "resource_client_id": "https://api.example.com",
            "resource_server_url": "https://api.example.com"
        }
        return mock_request_context

    def create_mock_request_context_without_auth(self):
        """Helper to create a mock RequestContext without authentication info."""
        mock_request_context = Mock(spec=RequestContext)
        mock_request_context.request = Mock()
        mock_request_context.request.state = {}
        return mock_request_context

    @pytest.mark.asyncio
    async def test_context_extraction_from_fastmcp_context(self, auth_provider_config, mock_client_factory):
        """Test context extraction when FastMCP Context is provided."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"]}
            return {"success": True}

        # Create mock Context with request_context
        mock_context = Mock(spec=Context)
        mock_request_context = self.create_mock_request_context_with_auth()
        mock_context.request_context = mock_request_context

        result = await test_function(ctx=mock_context, user_id="test_user")
        # Should work correctly now
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_context_extraction_from_request_context_directly(self, auth_provider_config, mock_client_factory):
        """Test context extraction when RequestContext is provided directly."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: RequestContext, user_id: str) -> dict:
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"]}
            return {"success": True}

        mock_request_context = self.create_mock_request_context_with_auth()

        result = await test_function(ctx=mock_request_context, user_id="test_user")
        # Should work correctly now
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_missing_auth_info_in_context(self, auth_provider_config, mock_client_factory):
        """Test behavior when context lacks authentication info."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"]}
            return {"success": True}

        # Create mock Context without auth info
        mock_context = Mock(spec=Context)
        mock_request_context = self.create_mock_request_context_without_auth()
        mock_context.request_context = mock_request_context

        result = await test_function(ctx=mock_context, user_id="test_user")

        assert "error" in result
        assert "No request authentication information available" in result["error"]


class TestGrantDecoratorParameterInjection:
    """Test grant decorator's parameter injection and argument handling."""

    def create_mock_context_with_auth(self):
        """Helper to create a mock Context with authentication info."""
        mock_context = Mock(spec=Context)
        mock_context.request_context = Mock()
        mock_context.request_context.request = Mock()
        mock_context.request_context.request.state.keycardai_auth_info = {
            "access_token": "test_token",
            "zone_id": "test123",
            "resource_client_id": "https://api.example.com",
            "resource_server_url": "https://api.example.com"
        }
        return mock_context

    @pytest.mark.asyncio
    async def test_access_context_injection_when_none_provided(self, auth_provider_config, mock_client_factory):
        """Test that AccessContext is injected when None is provided in kwargs."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            assert isinstance(access_ctx, AccessContext)
            return {"success": True, "access_ctx_type": type(access_ctx).__name__}

        mock_context = self.create_mock_context_with_auth()

        # Should create new AccessContext when None is provided
        result = await test_function(access_ctx=None, ctx=mock_context, user_id="test_user")
        assert result["success"] is True
        assert result["access_ctx_type"] == "AccessContext"

    @pytest.mark.asyncio
    async def test_access_context_preserved_when_provided(self, auth_provider_config, mock_client_factory):
        """Test that provided AccessContext is preserved and used."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            # Add a marker to verify this is our custom AccessContext
            access_ctx._test_marker = "custom_context"
            return {"success": True, "has_marker": hasattr(access_ctx, "_test_marker")}

        mock_context = self.create_mock_context_with_auth()
        custom_access_ctx = AccessContext()

        # Should preserve the provided AccessContext
        result = await test_function(access_ctx=custom_access_ctx, ctx=mock_context, user_id="test_user")
        assert result["success"] is True
        assert result["has_marker"] is True

    @pytest.mark.asyncio
    async def test_parameter_order_with_positional_args(self, auth_provider_config, mock_client_factory):
        """Test that parameter order is preserved with positional arguments."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str, extra_param: str = "default") -> dict:
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"]}
            return {
                "success": True,
                "user_id": user_id,
                "extra_param": extra_param,
                "access_ctx_type": type(access_ctx).__name__
            }

        mock_context = self.create_mock_context_with_auth()

        # Call with positional args in correct order
        result = await test_function(AccessContext(), mock_context, "test_user", "custom_value")

        # Should work correctly now
        assert result["success"] is True
        assert result["user_id"] == "test_user"
        assert result["extra_param"] == "custom_value"
        assert result["access_ctx_type"] == "AccessContext"

    @pytest.mark.asyncio
    async def test_mixed_args_and_kwargs(self, auth_provider_config, mock_client_factory):
        """Test function calls with mixed positional and keyword arguments."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str, extra_param: str = "default") -> dict:
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"]}
            return {
                "success": True,
                "user_id": user_id,
                "extra_param": extra_param
            }

        mock_context = self.create_mock_context_with_auth()

        # Call with mixed args: positional access_ctx, keyword ctx, positional user_id, keyword extra_param
        result = await test_function(AccessContext(), ctx=mock_context, user_id="test_user", extra_param="mixed_call")

        # Should work correctly now
        assert result["success"] is True
        assert result["user_id"] == "test_user"
        assert result["extra_param"] == "mixed_call"

    @pytest.mark.asyncio
    async def test_access_context_missing_key_vs_none_value(self, auth_provider_config, mock_client_factory):
        """Test that decorator correctly handles missing key vs None value for AccessContext."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            return {
                "success": True,
                "access_ctx": access_ctx,
                "user_id": user_id
            }

        mock_context = self.create_mock_context_with_auth()

        # Test 1: Missing key - should create new AccessContext
        result1 = await test_function(ctx=mock_context, user_id="test_user")

        # Test 2: None value - should create new AccessContext
        result2 = await test_function(access_ctx=None, ctx=mock_context, user_id="test_user")

        # Both should succeed and create different AccessContext instances
        assert result1["success"] is True
        assert result2["success"] is True
        assert id(result1["access_ctx"]) != id(result2["access_ctx"])  # Different instances


class TestGrantDecoratorEdgeCases:
    """Test edge cases and boundary conditions for the grant decorator."""

    def create_mock_context_with_auth(self):
        """Helper to create a mock Context with authentication info."""
        mock_context = Mock(spec=Context)
        mock_context.request_context = Mock()
        mock_context.request_context.request = Mock()
        mock_context.request_context.request.state.keycardai_auth_info = {
            "access_token": "test_token",
            "zone_id": "test123",
            "resource_client_id": "https://api.example.com",
            "resource_server_url": "https://api.example.com"
        }
        return mock_context

    @pytest.mark.asyncio
    async def test_access_context_parameter_order_variations(self, auth_provider_config, mock_client_factory):
        """Test that AccessContext parameter works regardless of its position in function signature."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        # AccessContext first
        @auth_provider.grant("https://api1.example.com")
        def func1(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            return {"order": "first", "success": True}

        # AccessContext middle
        @auth_provider.grant("https://api2.example.com")
        def func2(ctx: Context, access_ctx: AccessContext, user_id: str) -> dict:
            return {"order": "middle", "success": True}

        # AccessContext last
        @auth_provider.grant("https://api3.example.com")
        def func3(ctx: Context, user_id: str, access_ctx: AccessContext) -> dict:
            return {"order": "last", "success": True}

        mock_context = self.create_mock_context_with_auth()

        result1 = await func1(ctx=mock_context, user_id="test")
        result2 = await func2(ctx=mock_context, user_id="test")
        result3 = await func3(ctx=mock_context, user_id="test")

        assert result1["success"] is True
        assert result2["success"] is True
        assert result3["success"] is True

    @pytest.mark.asyncio
    async def test_multiple_resources_token_exchange(self, auth_provider_config, mock_client_factory):
        """Test decorator with multiple resources for token exchange."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant(["https://api1.example.com", "https://api2.example.com"])
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str) -> dict:
            if access_ctx.has_error():
                error = access_ctx.get_error()
                return {"error": error["error"]}

            # Try to access both resources
            try:
                token1 = access_ctx.access("https://api1.example.com").access_token
                token2 = access_ctx.access("https://api2.example.com").access_token
                return {
                    "success": True,
                    "token1": token1,
                    "token2": token2,
                    "user_id": user_id
                }
            except Exception as e:
                return {"error": str(e), "success": False}

        mock_context = self.create_mock_context_with_auth()

        result = await test_function(ctx=mock_context, user_id="test_user")

        # Should successfully exchange tokens for both resources
        assert result["success"] is True
        assert result["token1"] == "token_api1_123"  # From mock_client_factory
        assert result["token2"] == "token_api2_456"  # From mock_client_factory


class TestGrantDecoratorSignaturePreservation:
    """Test that the decorator preserves function signatures correctly."""

    def test_signature_excludes_access_context(self, auth_provider_config, mock_client_factory):
        """Test that the decorated function's signature excludes AccessContext parameter."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(access_ctx: AccessContext, ctx: Context, user_id: str, optional_param: str = "default") -> str:
            return f"Hello {user_id}"

        # Get the signature of the decorated function
        sig = inspect.signature(test_function)
        param_names = list(sig.parameters.keys())

        # AccessContext should be excluded from the signature
        assert "access_ctx" not in param_names
        assert "ctx" in param_names
        assert "user_id" in param_names
        assert "optional_param" in param_names

        # Check parameter details
        assert sig.parameters["ctx"].annotation == Context
        assert sig.parameters["user_id"].annotation is str
        assert sig.parameters["optional_param"].default == "default"

    def test_signature_preservation_with_different_parameter_order(self, auth_provider_config, mock_client_factory):
        """Test signature preservation when AccessContext is not the first parameter."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(ctx: Context, access_ctx: AccessContext, user_id: str) -> str:
            return f"Hello {user_id}"

        sig = inspect.signature(test_function)
        param_names = list(sig.parameters.keys())

        # AccessContext should be excluded, order should be preserved for remaining params
        assert param_names == ["ctx", "user_id"]
        assert "access_ctx" not in param_names

    def test_signature_with_complex_annotations(self, auth_provider_config, mock_client_factory):
        """Test signature preservation with complex type annotations."""
        auth_provider = AuthProvider(
            **auth_provider_config,
            client_factory=mock_client_factory
        )

        @auth_provider.grant("https://api.example.com")
        def test_function(
            access_ctx: AccessContext,
            ctx: Context,
            user_data: dict[str, Any],
            callback: callable = None
        ) -> dict[str, str]:
            return {"status": "ok"}

        sig = inspect.signature(test_function)

        # Check that complex annotations are preserved
        assert sig.parameters["user_data"].annotation == dict[str, Any]
        assert sig.parameters["callback"].annotation == callable
        assert sig.return_annotation == dict[str, str]
        assert "access_ctx" not in sig.parameters
