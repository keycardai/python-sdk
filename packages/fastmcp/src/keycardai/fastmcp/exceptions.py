"""Exceptions for the Keycard FastMCP integration.

Framework-free exceptions come from keycardai.oauth.server.exceptions.
``MissingContextError`` is defined here because its guidance references
FastMCP ``Context``.
"""

from __future__ import annotations

from keycardai.oauth.server.exceptions import OAuthServerError

__all__ = ["MissingContextError"]


class MissingContextError(OAuthServerError):
    """Raised when the grant decorator encounters a missing context error."""

    def __init__(
        self,
        message: str | None = None,
        *,
        function_name: str | None = None,
        parameters: list[str] | None = None,
        runtime_context: bool = False,
    ):
        if message is None:
            func_info = f"'{function_name}'" if function_name else "function"

            if runtime_context:
                message = (
                    f"Context parameter not found in {func_info} arguments.\n\n"
                    "This error occurs when:\n"
                    "1. Context parameter is not properly annotated with type hint\n"
                    "2. Context is not passed when calling the function\n\n"
                    "Ensure your function signature looks like:\n"
                    "  from fastmcp import Context\n\n"
                    f"  async def {function_name or 'your_function'}(ctx: Context, ...):  # <- Context must be type-hinted\n\n"
                    "FastMCP injects Context on tool calls; when calling the "
                    "function directly, pass a Context explicitly."
                )
            else:
                message = (
                    f"Function {func_info} must have a Context parameter to use @grant decorator.\n\n"
                    "The @grant decorator requires access to Context to store access tokens.\n\n"
                    "Fix by adding Context parameter:\n"
                    "  from fastmcp import Context\n\n"
                    "  @auth_provider.grant('https://api.example.com')\n"
                    f"  async def {function_name or 'your_function'}(ctx: Context, ...):  # <- Add 'ctx: Context' parameter\n"
                    "      access_context = await ctx.get_state('keycardai')\n"
                    "      # ... rest of function"
                )

        details = {
            "function_name": function_name or "unknown",
            "current_parameters": parameters or [],
            "runtime_context": runtime_context,
            "solution": (
                "Add 'ctx: Context' parameter to function signature"
                if not runtime_context
                else "Ensure Context parameter is properly type-hinted and passed"
            ),
        }

        super().__init__(message, details=details)
