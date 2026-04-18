"""Backward-compatible re-export from keycardai.starlette_oauth.routers."""

from keycardai.starlette_oauth.routers import auth_metadata_mount, protected_router

# Keep the MCP-specific name as an alias
protected_mcp_router = protected_router

__all__ = [
    "auth_metadata_mount",
    "protected_mcp_router",
    "protected_router",
]
