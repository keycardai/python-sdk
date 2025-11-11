"""Type-safe storage facades for auth strategies and coordinator state management."""

from datetime import timedelta
from typing import Any

from ..storage import NamespacedStorage


class OAuthStorage:
    """
    Type-safe facade for OAuth strategy storage.

    Encapsulates the storage keys and provides a clean API
    for storing/retrieving OAuth-related data.

    Storage hierarchy:
    ```
    oauth/
      ├─ tokens           # OAuth access/refresh tokens
      ├─ client_info      # Dynamic client registration info
      ├─ _pkce_state:{state}  # PKCE verifiers (TTL)
      └─ _auth_server_metadata  # Cached AS metadata
    ```
    """

    def __init__(self, base_storage: NamespacedStorage):
        """
        Initialize OAuth storage facade.

        Args:
            base_storage: Base namespaced storage (at oauth strategy level)
        """
        self._storage = base_storage

    async def save_tokens(self, tokens: dict[str, Any]) -> None:
        """
        Save OAuth tokens.

        Args:
            tokens: Token dict with access_token, refresh_token, etc.
        """
        await self._storage.set("tokens", tokens)

    async def get_tokens(self) -> dict[str, Any] | None:
        """
        Retrieve OAuth tokens.

        Returns:
            Token dict if present, None otherwise
        """
        return await self._storage.get("tokens")

    async def delete_tokens(self) -> None:
        """Delete stored tokens."""
        await self._storage.delete("tokens")

    async def save_client_registration(self, client_info: dict[str, Any]) -> None:
        """
        Save dynamic client registration information.

        Args:
            client_info: Client registration data (client_id, redirect_uris, etc.)
        """
        await self._storage.set("client_info", client_info)

    async def get_client_registration(self) -> dict[str, Any] | None:
        """
        Retrieve client registration information.

        Returns:
            Client info dict if registered, None otherwise
        """
        return await self._storage.get("client_info")

    async def save_pkce_state(
        self,
        state: str,
        pkce_data: dict[str, Any],
        ttl: timedelta
    ) -> None:
        """
        Save PKCE state for an OAuth flow.

        Args:
            state: OAuth state parameter (routing key)
            pkce_data: PKCE verifier and code challenge data
            ttl: Time-to-live for the PKCE state
        """
        await self._storage.set(f"_pkce_state:{state}", pkce_data, ttl=ttl)

    async def get_pkce_state(self, state: str) -> dict[str, Any] | None:
        """
        Retrieve PKCE state for an OAuth flow.

        Args:
            state: OAuth state parameter

        Returns:
            PKCE data dict if present, None if not found or expired
        """
        return await self._storage.get(f"_pkce_state:{state}")

    async def delete_pkce_state(self, state: str) -> None:
        """
        Delete PKCE state after token exchange.

        Args:
            state: OAuth state parameter
        """
        await self._storage.delete(f"_pkce_state:{state}")

    async def save_auth_server_metadata(
        self,
        metadata: dict[str, Any],
        ttl: timedelta | None = None
    ) -> None:
        """
        Cache authorization server metadata.

        Args:
            metadata: AS metadata (authorization_endpoint, token_endpoint, etc.)
            ttl: Time-to-live for the cached metadata (default: 1 hour)
        """
        if ttl is None:
            ttl = timedelta(hours=1)
        await self._storage.set("_auth_server_metadata", metadata, ttl=ttl)

    async def get_auth_server_metadata(self) -> dict[str, Any] | None:
        """
        Retrieve cached authorization server metadata.

        Returns:
            AS metadata dict if cached, None otherwise
        """
        return await self._storage.get("_auth_server_metadata")


class APIKeyStorage:
    """
    Type-safe facade for API key strategy storage.

    Storage hierarchy:
    ```
    api_key/
      └─ key  # The API key value
    ```
    """

    def __init__(self, base_storage: NamespacedStorage):
        """
        Initialize API key storage facade.

        Args:
            base_storage: Base namespaced storage (at api_key strategy level)
        """
        self._storage = base_storage

    async def save_key(self, api_key: str) -> None:
        """
        Save API key.

        Args:
            api_key: The API key value
        """
        await self._storage.set("key", api_key)

    async def get_key(self) -> str | None:
        """
        Retrieve API key.

        Returns:
            API key string if present, None otherwise
        """
        return await self._storage.get("key")

    async def delete_key(self) -> None:
        """Delete stored API key."""
        await self._storage.delete("key")


class AuthStateStorage:
    """
    Type-safe facade for auth coordinator state management.

    Encapsulates storage keys for pending auth state and completion routing,
    providing a clean API without magic strings.

    Storage hierarchy:
    ```
    auth_coordinator/
      ├─ pending_auth:{context_id}:{server_name}  # Pending auth metadata
      └─ completion:{routing_key}                 # Completion routing metadata
    ```
    """

    def __init__(self, base_storage: NamespacedStorage):
        """
        Initialize auth state storage facade.

        Args:
            base_storage: Base namespaced storage (at auth_coordinator level)
        """
        self._storage = base_storage

    async def mark_auth_pending(
        self,
        context_id: str,
        server_name: str,
        metadata: dict[str, Any],
        ttl: timedelta | None = None
    ) -> None:
        """
        Mark authentication as pending for a context/server.

        Called by strategies when they initiate an auth flow that requires
        user interaction (e.g., OAuth redirect). Stores metadata so sessions
        can detect pending auth and retrieve details.

        Args:
            context_id: Context identifier
            server_name: Server name requiring auth
            metadata: Strategy-specific metadata (e.g., authorization_url, state)
            ttl: Optional expiration time
        """
        key = f"pending_auth:{context_id}:{server_name}"
        await self._storage.set(key, metadata, ttl=ttl)

    async def get_auth_status(
        self,
        context_id: str,
        server_name: str
    ) -> dict[str, Any] | None:
        """
        Get pending authentication metadata for a context/server.

        Args:
            context_id: Context identifier
            server_name: Server name to check

        Returns:
            Auth metadata if pending, None otherwise
        """
        key = f"pending_auth:{context_id}:{server_name}"
        return await self._storage.get(key)

    async def clear_auth_status(
        self,
        context_id: str,
        server_name: str
    ) -> None:
        """
        Clear pending authentication state.

        Called by strategies when auth flow completes (successfully or not).

        Args:
            context_id: Context identifier
            server_name: Server name
        """
        key = f"pending_auth:{context_id}:{server_name}"
        await self._storage.delete(key)

    async def register_completion_route(
        self,
        routing_key: str,
        metadata: dict[str, Any],
        ttl: timedelta | None = None
    ) -> None:
        """
        Register completion routing metadata for stateless execution.

        Used in stateless environments where a different process invocation
        will handle the completion. Stores metadata needed to route the completion
        to the right handler and reconstruct necessary state.

        Args:
            routing_key: Key to route completion (e.g., OAuth state parameter)
            metadata: Routing metadata including:
                - handler_name: Name of registered completion handler
                - storage_namespace: Storage namespace path for strategy data
                - context_id: Context identifier
                - server_name: Server name for auth pending cleanup
                - handler_kwargs: Optional kwargs for completion handler
            ttl: Expiration time for routing metadata
        """
        key = f"completion:{routing_key}"
        await self._storage.set(key, metadata, ttl=ttl)

    async def get_completion_route(
        self,
        routing_key: str
    ) -> dict[str, Any] | None:
        """
        Get completion routing metadata.

        Args:
            routing_key: Routing key (e.g., OAuth state parameter)

        Returns:
            Routing metadata dict if registered, None if not found or expired
        """
        key = f"completion:{routing_key}"
        return await self._storage.get(key)

    async def delete_completion_route(
        self,
        routing_key: str
    ) -> None:
        """
        Delete completion routing metadata after completion.

        Args:
            routing_key: Routing key to clean up
        """
        key = f"completion:{routing_key}"
        await self._storage.delete(key)

