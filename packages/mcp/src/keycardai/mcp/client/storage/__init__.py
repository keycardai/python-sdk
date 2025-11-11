"""Storage abstractions for MCP client."""

from .backend import StorageBackend
from .backends import InMemoryBackend, SQLiteBackend
from .namespaced import NamespacedStorage
from .path_builder import StoragePathBuilder

__all__ = [
    "StorageBackend",
    "InMemoryBackend",
    "NamespacedStorage",
    "SQLiteBackend",
    "StoragePathBuilder",
]

