"""Backward-compatible re-export from keycardai.starlette.handlers."""

from keycardai.starlette.handlers.metadata import (
    ProtectedResourceMetadata as InferredProtectedResourceMetadata,
    authorization_server_metadata,
    protected_resource_metadata,
)

__all__ = [
    "InferredProtectedResourceMetadata",
    "authorization_server_metadata",
    "protected_resource_metadata",
]
