"""Storage path builder for type-safe namespace navigation."""

from .namespaced import NamespacedStorage


class StoragePathBuilder:
    """
    Builder for type-safe storage namespace navigation.

    Provides a fluent interface for navigating through storage namespaces
    with named methods instead of magic strings.

    Example:
        >>> storage = context.storage_path() \\
        ...     .for_server("slack") \\
        ...     .for_connection() \\
        ...     .for_oauth() \\
        ...     .build()
        >>> # Resulting namespace: "client:user:server:slack:connection:oauth"
    """

    def __init__(self, base_storage: NamespacedStorage):
        """
        Initialize storage path builder.

        Args:
            base_storage: Starting storage namespace
        """
        self._storage = base_storage
        self._path_parts: list[str] = []

    def for_server(self, server_name: str) -> "StoragePathBuilder":
        """
        Navigate to server namespace.

        Args:
            server_name: Server identifier (e.g., "slack", "github")

        Returns:
            Self for method chaining
        """
        self._storage = self._storage.get_namespace(f"server:{server_name}")
        self._path_parts.append(f"server:{server_name}")
        return self

    def for_connection(self) -> "StoragePathBuilder":
        """
        Navigate to connection namespace.

        Returns:
            Self for method chaining
        """
        self._storage = self._storage.get_namespace("connection")
        self._path_parts.append("connection")
        return self

    def for_oauth(self) -> "StoragePathBuilder":
        """
        Navigate to OAuth strategy namespace.

        Returns:
            Self for method chaining
        """
        self._storage = self._storage.get_namespace("oauth")
        self._path_parts.append("oauth")
        return self

    def for_api_key(self) -> "StoragePathBuilder":
        """
        Navigate to API key strategy namespace.

        Returns:
            Self for method chaining
        """
        self._storage = self._storage.get_namespace("api_key")
        self._path_parts.append("api_key")
        return self

    def for_namespace(self, namespace: str) -> "StoragePathBuilder":
        """
        Navigate to a custom namespace.

        Args:
            namespace: Custom namespace identifier

        Returns:
            Self for method chaining
        """
        self._storage = self._storage.get_namespace(namespace)
        self._path_parts.append(namespace)
        return self

    def build(self) -> NamespacedStorage:
        """
        Get the final storage namespace.

        Returns:
            NamespacedStorage at the built path
        """
        return self._storage

    def get_full_path(self) -> str:
        """
        Get the full namespace path (for logging/debugging).

        Returns:
            Colon-separated namespace path

        Example:
            >>> builder.for_server("slack").for_connection().get_full_path()
            "server:slack:connection"
        """
        return ":".join(self._path_parts)

