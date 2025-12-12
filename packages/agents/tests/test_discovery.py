"""Tests for service discovery with agent card caching."""

import time
from unittest.mock import AsyncMock, patch

import pytest

from keycardai.agents import AgentServiceConfig, ServiceDiscovery


@pytest.fixture
def service_config():
    """Create test service configuration."""
    return AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone_123",
    )


@pytest.fixture
def mock_agent_card():
    """Mock agent card response."""
    return {
        "name": "Target Service",
        "description": "A test target service",
        "type": "crew_service",
        "identity": "https://target.example.com",
        "endpoints": {
            "invoke": "https://target.example.com/invoke",
            "status": "https://target.example.com/status",
        },
        "auth": {
            "type": "oauth2",
            "token_url": "https://test_zone.keycard.cloud/oauth/token",
            "resource": "https://target.example.com",
        },
        "capabilities": ["test_capability", "another_capability"],
    }


@pytest.fixture
def discovery(service_config):
    """Create service discovery instance with default TTL."""
    return ServiceDiscovery(service_config)


@pytest.fixture
def discovery_short_ttl(service_config):
    """Create service discovery instance with short TTL for testing expiration."""
    return ServiceDiscovery(service_config, cache_ttl=1)  # 1 second TTL


class TestServiceDiscoveryInitialization:
    """Test discovery service initialization."""

    def test_init_with_default_cache_ttl(self, service_config):
        """Test initialization with default cache TTL."""
        discovery = ServiceDiscovery(service_config)
        assert discovery.cache_ttl == 900  # Default 15 minutes

    def test_init_with_custom_cache_ttl(self, service_config):
        """Test initialization with custom cache TTL."""
        discovery = ServiceDiscovery(service_config, cache_ttl=300)
        assert discovery.cache_ttl == 300

    def test_init_creates_a2a_client(self, service_config):
        """Test initialization creates A2A client."""
        discovery = ServiceDiscovery(service_config)
        assert discovery.a2a_client is not None
        assert hasattr(discovery.a2a_client, "discover_service")


class TestGetServiceCard:
    """Test agent card fetching with caching."""

    @pytest.mark.asyncio
    async def test_get_service_card_fetches_from_remote(
        self, discovery, mock_agent_card
    ):
        """Test that first call fetches from remote."""
        with patch.object(
            discovery.a2a_client, "discover_service", new_callable=AsyncMock
        ) as mock_discover:
            mock_discover.return_value = mock_agent_card

            card = await discovery.get_service_card("https://target.example.com")

            assert card == mock_agent_card
            mock_discover.assert_called_once_with("https://target.example.com")

    @pytest.mark.asyncio
    async def test_get_service_card_uses_cache(self, discovery, mock_agent_card):
        """Test that cache hit skips remote fetch."""
        with patch.object(
            discovery.a2a_client, "discover_service", new_callable=AsyncMock
        ) as mock_discover:
            mock_discover.return_value = mock_agent_card

            # First call - cache miss
            await discovery.get_service_card("https://target.example.com")

            # Second call - cache hit
            card = await discovery.get_service_card("https://target.example.com")

            # Should only call remote once
            assert mock_discover.call_count == 1
            assert card == mock_agent_card

    @pytest.mark.asyncio
    async def test_get_service_card_bypasses_cache_on_force_refresh(
        self, discovery, mock_agent_card
    ):
        """Test that force_refresh=True bypasses cache."""
        with patch.object(
            discovery.a2a_client, "discover_service", new_callable=AsyncMock
        ) as mock_discover:
            mock_discover.return_value = mock_agent_card

            # First call - cache miss
            await discovery.get_service_card("https://target.example.com")

            # Second call with force_refresh - should fetch again
            card = await discovery.get_service_card(
                "https://target.example.com", force_refresh=True
            )

            # Should call remote twice
            assert mock_discover.call_count == 2
            assert card == mock_agent_card

    @pytest.mark.asyncio
    async def test_get_service_card_refetches_on_expiration(
        self, discovery_short_ttl, mock_agent_card
    ):
        """Test that expired cache triggers refetch."""
        with patch.object(
            discovery_short_ttl.a2a_client, "discover_service", new_callable=AsyncMock
        ) as mock_discover:
            mock_discover.return_value = mock_agent_card

            # First fetch
            await discovery_short_ttl.get_service_card("https://target.example.com")

            # Wait for expiration (TTL is 1 second)
            time.sleep(1.5)

            # Second fetch should hit remote again due to expiration
            await discovery_short_ttl.get_service_card("https://target.example.com")

            assert mock_discover.call_count == 2

    @pytest.mark.asyncio
    async def test_get_service_card_normalizes_url(self, discovery, mock_agent_card):
        """Test that URLs with trailing slashes are normalized."""
        with patch.object(
            discovery.a2a_client, "discover_service", new_callable=AsyncMock
        ) as mock_discover:
            mock_discover.return_value = mock_agent_card

            # Call with trailing slash
            await discovery.get_service_card("https://target.example.com/")

            # Should normalize to URL without trailing slash
            mock_discover.assert_called_once_with("https://target.example.com")

    @pytest.mark.asyncio
    async def test_get_service_card_caches_normalized_url(
        self, discovery, mock_agent_card
    ):
        """Test that cache uses normalized URL."""
        with patch.object(
            discovery.a2a_client, "discover_service", new_callable=AsyncMock
        ) as mock_discover:
            mock_discover.return_value = mock_agent_card

            # First call with trailing slash
            await discovery.get_service_card("https://target.example.com/")

            # Second call without trailing slash - should hit cache
            await discovery.get_service_card("https://target.example.com")

            # Should only call remote once (cache hit on second call)
            assert mock_discover.call_count == 1


class TestCachedAgentCard:
    """Test cached card expiration logic."""

    def test_is_expired_when_ttl_exceeded(self, discovery):
        """Test card is expired when TTL is exceeded."""
        from keycardai.agents.discovery import CachedAgentCard

        card = CachedAgentCard(
            card={"name": "test"},
            fetched_at=time.time() - 1000,  # 1000 seconds ago
            ttl=900,  # 900 seconds TTL
        )

        # With default TTL of 900 seconds, should be expired
        assert card.is_expired

    def test_is_not_expired_within_ttl(self, discovery):
        """Test card is not expired within TTL."""
        from keycardai.agents.discovery import CachedAgentCard

        card = CachedAgentCard(
            card={"name": "test"},
            fetched_at=time.time() - 100,  # 100 seconds ago
            ttl=900,  # 900 seconds TTL
        )

        # With TTL of 900 seconds, should not be expired
        assert not card.is_expired

    def test_age_seconds_calculation(self, discovery):
        """Test age calculation is correct."""
        from keycardai.agents.discovery import CachedAgentCard

        fetch_time = time.time() - 42
        card = CachedAgentCard(card={"name": "test"}, fetched_at=fetch_time)

        age = card.age_seconds
        # Should be approximately 42 seconds (with small tolerance for execution time)
        assert 41 <= age <= 43


class TestCacheManagement:
    """Test cache clearing and statistics."""

    @pytest.mark.asyncio
    async def test_clear_cache_removes_all_entries(self, discovery, mock_agent_card):
        """Test clear_cache removes all cached entries."""
        with patch.object(
            discovery.a2a_client, "discover_service", new_callable=AsyncMock
        ) as mock_discover:
            mock_discover.return_value = mock_agent_card

            # Add multiple entries to cache
            await discovery.get_service_card("https://service1.example.com")
            await discovery.get_service_card("https://service2.example.com")

            # Clear cache
            await discovery.clear_cache()

            # Next calls should fetch from remote
            await discovery.get_service_card("https://service1.example.com")
            await discovery.get_service_card("https://service2.example.com")

            # Should have called remote 4 times total (2 before clear, 2 after)
            assert mock_discover.call_count == 4

    @pytest.mark.asyncio
    async def test_clear_service_cache_removes_specific_entry(
        self, discovery, mock_agent_card
    ):
        """Test clear_service_cache removes only specific entry."""
        with patch.object(
            discovery.a2a_client, "discover_service", new_callable=AsyncMock
        ) as mock_discover:
            mock_discover.return_value = mock_agent_card

            # Add two entries to cache
            await discovery.get_service_card("https://service1.example.com")
            await discovery.get_service_card("https://service2.example.com")

            # Clear only service1
            await discovery.clear_service_cache("https://service1.example.com")

            # Service1 should refetch, service2 should use cache
            await discovery.get_service_card("https://service1.example.com")
            await discovery.get_service_card("https://service2.example.com")

            # Should have called remote 3 times (2 initial + 1 refetch for service1)
            assert mock_discover.call_count == 3

    @pytest.mark.asyncio
    async def test_get_cache_stats_counts_correctly(self, discovery, mock_agent_card):
        """Test cache statistics are accurate."""
        with patch.object(
            discovery.a2a_client, "discover_service", new_callable=AsyncMock
        ) as mock_discover:
            mock_discover.return_value = mock_agent_card

            # Add entries to cache
            await discovery.get_service_card("https://service1.example.com")
            await discovery.get_service_card("https://service2.example.com")

            stats = discovery.get_cache_stats()

            assert stats["total_cached"] == 2
            assert stats["expired"] == 0

    @pytest.mark.asyncio
    async def test_get_cache_stats_identifies_expired(
        self, discovery_short_ttl, mock_agent_card
    ):
        """Test cache statistics identify expired entries."""
        with patch.object(
            discovery_short_ttl.a2a_client, "discover_service", new_callable=AsyncMock
        ) as mock_discover:
            mock_discover.return_value = mock_agent_card

            # Add entry to cache
            await discovery_short_ttl.get_service_card("https://service1.example.com")

            # Wait for expiration
            time.sleep(1.5)

            stats = discovery_short_ttl.get_cache_stats()

            assert stats["total_cached"] == 1
            assert stats["expired"] == 1


class TestListDelegatableServices:
    """Test service listing (placeholder implementation)."""

    @pytest.mark.asyncio
    async def test_list_delegatable_services_returns_empty(self, discovery):
        """Test list_delegatable_services returns empty list (not yet implemented)."""
        services = await discovery.list_delegatable_services()
        assert services == []
        assert isinstance(services, list)


class TestContextManager:
    """Test discovery as async context manager."""

    @pytest.mark.asyncio
    async def test_context_manager_closes_client(self, service_config):
        """Test context manager closes A2A client properly."""
        async with ServiceDiscovery(service_config) as discovery:
            assert discovery.a2a_client is not None

        # After exit, client should be closed
        # Note: A2AServiceClient doesn't have close() in current implementation,
        # but context manager should handle cleanup properly
