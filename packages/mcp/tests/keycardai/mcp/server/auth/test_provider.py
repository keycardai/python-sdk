"""Unit tests for the grant decorator in provider.py.

This module tests the grant decorator's parameter handling, signature validation,
and context injection behavior for various function signatures and call patterns.
"""

import inspect
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from mcp.server.fastmcp import Context
from mcp.shared.context import RequestContext

from keycardai.mcp.server.auth import (
    AccessContext,
    AuthProvider,
    ClientSecret,
    EKSWorkloadIdentity,
    MissingAccessContextError,
    MissingContextError,
    WebIdentity,
)
from keycardai.mcp.server.exceptions import AuthProviderConfigurationError


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


class TestAuthProviderCredentialDiscovery:
    """Unit tests for AuthProvider application credential discovery logic."""

    @pytest.fixture
    def temp_key_storage(self):
        """Fixture providing a temporary directory for WebIdentity key storage.

        Creates a temporary directory before tests and cleans it up after.
        """
        temp_dir = tempfile.mkdtemp(prefix="test_webidentity_keys_")
        yield temp_dir
        # Cleanup: remove the temporary directory and all its contents
        if Path(temp_dir).exists():
            shutil.rmtree(temp_dir)

    def test_discover_returns_provided_credential(self, mock_client_factory, temp_key_storage):
        """Test that provided application credential is returned as-is."""
        # Create a specific credential
        provided_credential = ClientSecret(("test_client_id", "test_secret"))

        # Create provider with the credential
        auth_provider = AuthProvider(
            zone_id="test123",
            application_credential=provided_credential,
            client_factory=mock_client_factory
        )

        # Should return the same credential
        result = auth_provider._discover_application_credential(provided_credential)
        assert result is provided_credential

    @patch.dict(os.environ, {
        "KEYCARD_CLIENT_ID": "test_client_id",
        "KEYCARD_CLIENT_SECRET": "test_secret"
    }, clear=True)
    def test_discover_from_client_id_secret_env_vars(self, mock_client_factory):
        """Test discovery of ClientSecret from environment variables."""
        auth_provider = AuthProvider(
            zone_id="test123",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return ClientSecret with correct credentials
        assert isinstance(result, ClientSecret)

    @patch.dict(os.environ, {"KEYCARD_CLIENT_ID": "test_id"}, clear=True)
    def test_discover_ignores_partial_client_credentials(self, mock_client_factory):
        """Test that only KEYCARD_CLIENT_ID without SECRET is ignored."""
        auth_provider = AuthProvider(
            zone_id="test123",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return None when only client_id is present
        assert result is None

    @patch.dict(os.environ, {"KEYCARD_CLIENT_SECRET": "test_secret"}, clear=True)
    def test_discover_ignores_secret_without_client_id(self, mock_client_factory):
        """Test that only KEYCARD_CLIENT_SECRET without ID is ignored."""
        auth_provider = AuthProvider(
            zone_id="test123",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return None when only client_secret is present
        assert result is None

    @patch.dict(os.environ, {
        "KEYCARD_APPLICATION_CREDENTIAL_TYPE": "eks_workload_identity",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/tmp/test_token"
    }, clear=True)
    @patch("builtins.open", create=True)
    def test_discover_eks_workload_identity_from_type_env(self, mock_open, mock_client_factory):
        """Test discovery of EKSWorkloadIdentity from credential type env var."""
        # Mock the token file read
        mock_open.return_value.__enter__.return_value.read.return_value = "test_token"

        auth_provider = AuthProvider(
            zone_id="test123",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return EKSWorkloadIdentity instance
        assert isinstance(result, EKSWorkloadIdentity)

    def test_discover_web_identity_from_type_env(self, mock_client_factory, temp_key_storage):
        """Test discovery of WebIdentity from credential type env var."""
        with patch.dict(os.environ, {
            "KEYCARD_APPLICATION_CREDENTIAL_TYPE": "web_identity",
            "KEYCARD_WEB_IDENTITY_KEY_STORAGE_DIR": temp_key_storage
        }, clear=True):
            auth_provider = AuthProvider(
                zone_id="test123",
                mcp_server_name="test_mcp_server",
                client_factory=mock_client_factory
            )

            result = auth_provider._discover_application_credential(None)

            # Should return WebIdentity instance
            assert isinstance(result, WebIdentity)

            # Verify key storage directory was used
            assert Path(temp_key_storage).exists()

    @patch.dict(os.environ, {"KEYCARD_APPLICATION_CREDENTIAL_TYPE": "unknown_type"}, clear=True)
    def test_discover_raises_error_for_unknown_credential_type(self, mock_client_factory):
        """Test that unknown credential type raises AuthProviderConfigurationError."""
        # Should raise error with helpful message during initialization
        with pytest.raises(AuthProviderConfigurationError) as exc_info:
            AuthProvider(
                zone_id="test123",
                client_factory=mock_client_factory
            )

        # Check error message contains useful information
        assert "Unknown application credential type: unknown_type" in str(exc_info.value)
        assert "eks_workload_identity" in str(exc_info.value)
        assert "web_identity" in str(exc_info.value)

    @patch.dict(os.environ, {"AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/tmp/test_token"}, clear=True)
    @patch("builtins.open", create=True)
    def test_discover_eks_workload_identity_from_token_file_env(self, mock_open, mock_client_factory):
        """Test discovery of EKSWorkloadIdentity from AWS token file env var."""
        # Mock the token file read
        mock_open.return_value.__enter__.return_value.read.return_value = "test_token"

        auth_provider = AuthProvider(
            zone_id="test123",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should detect and return EKSWorkloadIdentity
        assert isinstance(result, EKSWorkloadIdentity)

    @patch.dict(os.environ, {}, clear=True)
    def test_discover_returns_none_when_no_credentials_found(self, mock_client_factory):
        """Test that None is returned when no credentials are discoverable."""
        auth_provider = AuthProvider(
            zone_id="test123",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return None when nothing is configured
        assert result is None

    @patch.dict(os.environ, {
        "KEYCARD_CLIENT_ID": "env_client_id",
        "KEYCARD_CLIENT_SECRET": "env_secret",
        "KEYCARD_APPLICATION_CREDENTIAL_TYPE": "web_identity"
    }, clear=True)
    def test_discover_priority_client_credentials_over_type(self, mock_client_factory):
        """Test that KEYCARD_CLIENT_ID/SECRET take priority over credential type."""
        auth_provider = AuthProvider(
            zone_id="test123",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return ClientSecret, not WebIdentity
        assert isinstance(result, ClientSecret)

    @patch.dict(os.environ, {
        "KEYCARD_APPLICATION_CREDENTIAL_TYPE": "eks_workload_identity",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/tmp/test_token"
    }, clear=True)
    @patch("builtins.open", create=True)
    def test_discover_priority_explicit_type_over_detected(self, mock_open, mock_client_factory):
        """Test that explicit credential type takes priority over auto-detected."""
        # Mock the token file read
        mock_open.return_value.__enter__.return_value.read.return_value = "test_token"

        auth_provider = AuthProvider(
            zone_id="test123",
            client_factory=mock_client_factory
        )

        result = auth_provider._discover_application_credential(None)

        # Should return EKSWorkloadIdentity (though both paths lead to same result)
        assert isinstance(result, EKSWorkloadIdentity)

    def test_discover_provided_credential_ignores_env_vars(self, mock_client_factory, temp_key_storage):
        """Test that provided credential takes absolute priority over env vars."""
        provided_credential = WebIdentity(
            mcp_server_name="provided_server",
            storage_dir=temp_key_storage
        )

        with patch.dict(os.environ, {
            "KEYCARD_CLIENT_ID": "env_client_id",
            "KEYCARD_CLIENT_SECRET": "env_secret",
            "KEYCARD_APPLICATION_CREDENTIAL_TYPE": "eks_workload_identity",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/tmp/test_token"
        }, clear=True):
            auth_provider = AuthProvider(
                zone_id="test123",
                application_credential=provided_credential,
                client_factory=mock_client_factory
            )

            result = auth_provider._discover_application_credential(provided_credential)

            # Should return the provided credential, not anything from env
            assert result is provided_credential
            assert isinstance(result, WebIdentity)


class TestAuthProviderZoneConfigurationDiscovery:
    """Unit tests for AuthProvider zone configuration discovery logic."""

    @patch.dict(os.environ, {}, clear=True)
    def test_zone_id_from_explicit_parameter(self, mock_client_factory):
        """Test that explicit zone_id parameter takes priority."""
        auth_provider = AuthProvider(
            zone_id="explicit_zone",
            client_factory=mock_client_factory
        )

        # Should use the explicit zone_id to construct zone_url
        assert "explicit_zone" in auth_provider.zone_url
        assert auth_provider.zone_url == "https://explicit_zone.keycard.cloud"

    @patch.dict(os.environ, {"KEYCARD_ZONE_ID": "env_zone"}, clear=True)
    def test_zone_id_from_environment_variable(self, mock_client_factory):
        """Test discovery of zone_id from KEYCARD_ZONE_ID env var."""
        auth_provider = AuthProvider(
            client_factory=mock_client_factory
        )

        # Should discover zone_id from environment
        assert "env_zone" in auth_provider.zone_url
        assert auth_provider.zone_url == "https://env_zone.keycard.cloud"

    @patch.dict(os.environ, {"KEYCARD_ZONE_ID": "env_zone"}, clear=True)
    def test_explicit_zone_id_takes_priority_over_env(self, mock_client_factory):
        """Test that explicit zone_id parameter takes priority over env var."""
        auth_provider = AuthProvider(
            zone_id="explicit_zone",
            client_factory=mock_client_factory
        )

        # Should use explicit zone_id, not env var
        assert "explicit_zone" in auth_provider.zone_url
        assert "env_zone" not in auth_provider.zone_url

    @patch.dict(os.environ, {}, clear=True)
    def test_zone_url_from_explicit_parameter(self, mock_client_factory):
        """Test that explicit zone_url parameter is used directly."""
        auth_provider = AuthProvider(
            zone_url="https://custom.zone.example.com",
            client_factory=mock_client_factory
        )

        # Should use the explicit zone_url
        assert auth_provider.zone_url == "https://custom.zone.example.com"

    @patch.dict(os.environ, {"KEYCARD_ZONE_URL": "https://env.zone.example.com"}, clear=True)
    def test_zone_url_from_environment_variable(self, mock_client_factory):
        """Test discovery of zone_url from KEYCARD_ZONE_URL env var."""
        auth_provider = AuthProvider(
            client_factory=mock_client_factory
        )

        # Should discover zone_url from environment
        assert auth_provider.zone_url == "https://env.zone.example.com"

    @patch.dict(os.environ, {"KEYCARD_ZONE_URL": "https://env.zone.example.com"}, clear=True)
    def test_explicit_zone_url_takes_priority_over_env(self, mock_client_factory):
        """Test that explicit zone_url parameter takes priority over env var."""
        auth_provider = AuthProvider(
            zone_url="https://explicit.zone.example.com",
            client_factory=mock_client_factory
        )

        # Should use explicit zone_url, not env var
        assert auth_provider.zone_url == "https://explicit.zone.example.com"

    @patch.dict(os.environ, {}, clear=True)
    def test_base_url_from_explicit_parameter(self, mock_client_factory):
        """Test that explicit base_url parameter is used for zone construction."""
        auth_provider = AuthProvider(
            zone_id="test_zone",
            base_url="https://custom.keycard.example.com",
            client_factory=mock_client_factory
        )

        # Should use custom base_url to construct zone_url
        assert auth_provider.zone_url == "https://test_zone.custom.keycard.example.com"
        assert auth_provider.base_url == "https://custom.keycard.example.com"

    @patch.dict(os.environ, {"KEYCARD_BASE_URL": "https://env.keycard.example.com"}, clear=True)
    def test_base_url_from_environment_variable(self, mock_client_factory):
        """Test discovery of base_url from KEYCARD_BASE_URL env var."""
        auth_provider = AuthProvider(
            zone_id="test_zone",
            client_factory=mock_client_factory
        )

        # Should discover base_url from environment
        assert auth_provider.base_url == "https://env.keycard.example.com"
        assert auth_provider.zone_url == "https://test_zone.env.keycard.example.com"

    @patch.dict(os.environ, {"KEYCARD_BASE_URL": "https://env.keycard.example.com"}, clear=True)
    def test_explicit_base_url_takes_priority_over_env(self, mock_client_factory):
        """Test that explicit base_url parameter takes priority over env var."""
        auth_provider = AuthProvider(
            zone_id="test_zone",
            base_url="https://explicit.keycard.example.com",
            client_factory=mock_client_factory
        )

        # Should use explicit base_url, not env var
        assert auth_provider.base_url == "https://explicit.keycard.example.com"
        assert auth_provider.zone_url == "https://test_zone.explicit.keycard.example.com"

    @patch.dict(os.environ, {
        "KEYCARD_ZONE_ID": "env_zone",
        "KEYCARD_ZONE_URL": "https://env.zone.example.com",
        "KEYCARD_BASE_URL": "https://env.keycard.example.com"
    }, clear=True)
    def test_zone_url_takes_priority_over_zone_id(self, mock_client_factory):
        """Test that zone_url (explicit or env) takes priority over zone_id in construction."""
        auth_provider = AuthProvider(
            client_factory=mock_client_factory
        )

        # Should use zone_url from env, ignoring zone_id and base_url
        assert auth_provider.zone_url == "https://env.zone.example.com"

    @patch.dict(os.environ, {
        "KEYCARD_ZONE_ID": "env_zone",
        "KEYCARD_ZONE_URL": "https://env.zone.example.com"
    }, clear=True)
    def test_explicit_zone_url_takes_priority_over_all(self, mock_client_factory):
        """Test that explicit zone_url takes priority over all env vars."""
        auth_provider = AuthProvider(
            zone_url="https://explicit.zone.example.com",
            client_factory=mock_client_factory
        )

        # Should use explicit zone_url, ignoring all env vars
        assert auth_provider.zone_url == "https://explicit.zone.example.com"

    @patch.dict(os.environ, {}, clear=True)
    def test_raises_error_when_no_zone_configuration(self, mock_client_factory):
        """Test that AuthProviderConfigurationError is raised when no zone configuration is provided."""
        with pytest.raises(AuthProviderConfigurationError):
            AuthProvider(
                client_factory=mock_client_factory
            )
