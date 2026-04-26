"""Backward-compatible re-export from keycardai.starlette.routers."""

from keycardai.starlette.routers import auth_metadata_mount, protected_router

from .metadata import protected_mcp_router

__all__ = [
    "auth_metadata_mount",
    "protected_mcp_router",
    "protected_router",
]
