"""Service discovery and agent card caching."""

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import AgentServiceConfig

logger = logging.getLogger(__name__)


@dataclass
class CachedAgentCard:
    """Cached agent card with expiration time.

    Attributes:
        card: Agent card dictionary
        fetched_at: Unix timestamp when card was fetched
        ttl: Time-to-live in seconds
    """

    card: dict[str, Any]
    fetched_at: float
    ttl: int = 900  # 15 minutes default

    @property
    def is_expired(self) -> bool:
        """Check if cached card has expired."""
        return (time.time() - self.fetched_at) > self.ttl

    @property
    def age_seconds(self) -> float:
        """Get age of cached card in seconds."""
        return time.time() - self.fetched_at


class ServiceDiscovery:
    """Service discovery with agent card caching.

    Manages discovery of other agent services with caching to minimize
    network requests. Agent cards are cached for 15 minutes by default.

    Args:
        service_config: Configuration of the calling service
        cache_ttl: Cache time-to-live in seconds (default: 900 = 15 minutes)

    Example:
        >>> from keycardai.agents import AgentServiceConfig
        >>> from keycardai.agents.client import ServiceDiscovery
        >>> 
        >>> config = AgentServiceConfig(...)
        >>> discovery = ServiceDiscovery(config)
        >>>
        >>> # Discover service (cached)
        >>> card = await discovery.get_service_card("https://slack-poster.example.com")
        >>> print(card["capabilities"])
        >>>
        >>> # List all discoverable services from Keycard dependencies
        >>> services = await discovery.list_delegatable_services()
        >>> for service in services:
        ...     print(f"{service['name']}: {service['url']}")
    """

    def __init__(
        self,
        service_config: AgentServiceConfig,
        cache_ttl: int = 900,
    ):
        """Initialize service discovery with caching.

        Args:
            service_config: Configuration of the calling service
            cache_ttl: Cache time-to-live in seconds (default: 900)
        """
        self.config = service_config
        self.cache_ttl = cache_ttl

        # Agent card cache: service_url -> CachedAgentCard
        self._card_cache: dict[str, CachedAgentCard] = {}

        # HTTP client for fetching agent cards
        self.http_client = httpx.AsyncClient(timeout=30.0)

    async def discover_service(self, service_url: str) -> dict[str, Any]:
        """Fetch agent card from remote service.

        Fetches the agent card from the well-known endpoint to discover
        service capabilities, endpoints, and authentication requirements.

        Args:
            service_url: Base URL of the target service

        Returns:
            Agent card dictionary with service metadata

        Raises:
            httpx.HTTPStatusError: If agent card fetch fails
            ValueError: If agent card format is invalid

        Example:
            >>> card = await discovery.discover_service("https://slack-poster.example.com")
            >>> print(card["capabilities"])
            ['slack_posting', 'message_formatting']
        """
        # Ensure URL doesn't have trailing slash
        service_url = service_url.rstrip("/")

        # Fetch agent card from well-known endpoint
        agent_card_url = f"{service_url}/.well-known/agent-card.json"

        try:
            response = await self.http_client.get(agent_card_url)
            response.raise_for_status()

            card = response.json()

            # Validate required fields
            required_fields = ["name", "endpoints", "auth"]
            for field in required_fields:
                if field not in card:
                    raise ValueError(f"Invalid agent card: missing required field '{field}'")

            logger.info(f"Discovered service: {card.get('name')} at {service_url}")
            return card

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to fetch agent card from {agent_card_url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error discovering service at {service_url}: {e}")
            raise

    async def get_service_card(
        self,
        service_url: str,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Get agent card for a service (with caching).

        Fetches and caches the agent card from the target service.
        Uses cached version if available and not expired.

        Args:
            service_url: Base URL of the target service
            force_refresh: If True, bypass cache and fetch fresh card

        Returns:
            Agent card dictionary

        Raises:
            httpx.HTTPStatusError: If agent card fetch fails

        Example:
            >>> card = await discovery.get_service_card(
            ...     "https://slack-poster.example.com"
            ... )
            >>> print(card["capabilities"])
        """
        # Normalize URL
        service_url = service_url.rstrip("/")

        # Check cache
        if not force_refresh and service_url in self._card_cache:
            cached = self._card_cache[service_url]
            if not cached.is_expired:
                logger.debug(
                    f"Using cached agent card for {service_url} "
                    f"(age: {cached.age_seconds:.1f}s)"
                )
                return cached.card

        # Fetch fresh card
        logger.info(f"Fetching agent card for {service_url}")
        card = await self.discover_service(service_url)

        # Cache it
        self._card_cache[service_url] = CachedAgentCard(
            card=card,
            fetched_at=time.time(),
            ttl=self.cache_ttl,
        )

        return card

    async def list_delegatable_services(self) -> list[dict[str, Any]]:
        """List all services this service can delegate to.

        Queries Keycard to find all services that this service has
        dependencies configured for. Returns service information with
        their agent cards.

        Returns:
            List of service dictionaries with 'name', 'url', 'description', 'capabilities'

        Note:
            This requires a Keycard API endpoint that lists application dependencies.
            Currently returns empty list. Once Keycard API is available, it will query:
            GET https://{zone_id}.keycard.cloud/api/v1/applications/{client_id}/dependencies

            For now, use the `delegatable_services` parameter in `get_a2a_tools()`
            to manually specify services.

        Example:
            >>> services = await discovery.list_delegatable_services()
            >>> for service in services:
            ...     print(f"{service['name']}: {service['capabilities']}")
        """
        logger.warning(
            "list_delegatable_services() not yet implemented - "
            "requires Keycard API for dependency listing. "
            "Use delegatable_services parameter in get_a2a_tools() instead."
        )
        return []

    async def clear_cache(self) -> None:
        """Clear all cached agent cards."""
        logger.info("Clearing agent card cache")
        self._card_cache.clear()

    async def clear_service_cache(self, service_url: str) -> None:
        """Clear cached agent card for a specific service.

        Args:
            service_url: Base URL of the service
        """
        service_url = service_url.rstrip("/")
        if service_url in self._card_cache:
            logger.info(f"Clearing cached agent card for {service_url}")
            del self._card_cache[service_url]

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dictionary with cache size, hit rate, etc.
        """
        total_cached = len(self._card_cache)
        expired = sum(1 for cached in self._card_cache.values() if cached.is_expired)

        return {
            "total_cached": total_cached,
            "expired": expired,
            "active": total_cached - expired,
            "ttl_seconds": self.cache_ttl,
        }

    async def close(self) -> None:
        """Close underlying clients."""
        await self.http_client.aclose()

    async def __aenter__(self) -> "ServiceDiscovery":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()
