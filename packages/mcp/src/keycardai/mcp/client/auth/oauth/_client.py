"""Shared HTTP client factory for OAuth services."""

from httpx import AsyncClient


def default_client_factory() -> AsyncClient:
    """
    Default HTTP client factory for OAuth services.

    Returns a new AsyncClient instance for making HTTP requests
    to OAuth endpoints (discovery, registration, token exchange).

    Returns:
        AsyncClient: New HTTP client instance
    """
    return AsyncClient()

