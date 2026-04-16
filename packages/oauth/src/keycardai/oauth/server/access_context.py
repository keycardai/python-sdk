"""Access context for delegated token exchange.

Provides a non-throwing interface for accessing exchanged tokens.
Errors are stored per-resource rather than raised, enabling
partial-success scenarios where some resources succeed while others fail.
"""

from typing import Any

from keycardai.oauth.types.models import TokenResponse

from .exceptions import ResourceAccessError


class AccessContext:
    """Context object that provides access to exchanged tokens for specific resources.

    Supports both successful token storage and per-resource error tracking,
    allowing partial success scenarios where some resources succeed while others fail.
    """

    def __init__(self, access_tokens: dict[str, TokenResponse] | None = None):
        self._access_tokens: dict[str, TokenResponse] = access_tokens or {}
        self._resource_errors: dict[str, dict[str, str]] = {}
        self._error: dict[str, str] | None = None

    def set_bulk_tokens(self, access_tokens: dict[str, TokenResponse]):
        """Set access tokens for resources."""
        self._access_tokens.update(access_tokens)

    def set_token(self, resource: str, token: TokenResponse):
        """Set token for the specified resource."""
        self._access_tokens[resource] = token
        self._resource_errors.pop(resource, None)

    def set_resource_error(self, resource: str, error: dict[str, str]):
        """Set error for a specific resource."""
        self._resource_errors[resource] = error
        self._access_tokens.pop(resource, None)

    def set_error(self, error: dict[str, str]):
        """Set error that affects all resources."""
        self._error = error

    def has_resource_error(self, resource: str) -> bool:
        """Check if a specific resource has an error."""
        return resource in self._resource_errors

    def has_error(self) -> bool:
        """Check if there's a global error."""
        return self._error is not None

    def has_errors(self) -> bool:
        """Check if there are any errors (global or resource-specific)."""
        return self.has_error() or len(self._resource_errors) > 0

    def get_errors(self) -> dict[str, Any] | None:
        """Get global errors if any."""
        return {"resources": self._resource_errors.copy(), "error": self._error}

    def get_error(self) -> dict[str, str] | None:
        """Get global error if any."""
        return self._error

    def get_resource_errors(self, resource: str) -> dict[str, str] | None:
        """Get error for a specific resource."""
        return self._resource_errors.get(resource)

    def get_status(self) -> str:
        """Get overall status of the access context."""
        if self.has_error():
            return "error"
        elif self.has_errors():
            return "partial_error"
        else:
            return "success"

    def get_successful_resources(self) -> list[str]:
        """Get list of resources that have successful tokens."""
        return list(self._access_tokens.keys())

    def get_failed_resources(self) -> list[str]:
        """Get list of resources that have errors."""
        return list(self._resource_errors.keys())

    def access(self, resource: str) -> TokenResponse:
        """Get token response for the specified resource.

        Args:
            resource: The resource URL to get token response for

        Returns:
            TokenResponse object with access_token attribute

        Raises:
            ResourceAccessError: If resource was not granted or has an error
        """
        if self.has_error():
            raise ResourceAccessError()

        if self.has_resource_error(resource):
            raise ResourceAccessError()

        if resource not in self._access_tokens:
            raise ResourceAccessError()

        return self._access_tokens[resource]
