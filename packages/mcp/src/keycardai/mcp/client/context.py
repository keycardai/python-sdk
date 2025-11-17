"""Core context primitives for MCP client."""

from typing import TYPE_CHECKING, Any

from .storage import NamespacedStorage, StoragePathBuilder

if TYPE_CHECKING:
    from .auth.coordinators.base import AuthCoordinator


class Context:
    """
    Represents an isolated execution context.

    Provides:
    - Unique identifier (e.g., "user:alice")
    - Namespaced storage (isolated from other contexts)
    - Reference to auth coordinator
    - Type-safe storage path builder
    """

    def __init__(
        self,
        id: str,
        storage: NamespacedStorage,
        coordinator: "AuthCoordinator",
        metadata: dict[str, Any] | None = None
    ):
        """
        Initialize context.

        Args:
            id: Unique context identifier (e.g., "user:alice")
            storage: Namespaced storage for this context
            coordinator: Auth coordinator reference
            metadata: Optional metadata dict (e.g., user info, session data)
        """
        self.id = id
        self.storage = storage
        self.coordinator = coordinator
        self.metadata = metadata or {}

    def storage_path(self) -> StoragePathBuilder:
        """
        Create a storage path builder starting from this context's storage.

        Returns:
            StoragePathBuilder for fluent namespace navigation

        Example:
            >>> storage = context.storage_path() \\
            ...     .for_server("slack") \\
            ...     .for_connection() \\
            ...     .for_oauth() \\
            ...     .build()
        """
        return StoragePathBuilder(self.storage)

    def __repr__(self) -> str:
        return f"Context(id={self.id!r})"

