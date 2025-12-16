"""KeycardAI Agents - Agent service framework with authentication and delegation.

This package provides tools for building and consuming agent services with OAuth authentication:

Client (for calling agent services):
- AgentClient: User authentication with PKCE OAuth flow
- ServiceDiscovery: Discover and query agent service capabilities

Server (for building agent services):
- AgentServer: High-level server interface
- create_agent_card_server: Create FastAPI app with OAuth middleware
- serve_agent: Convenience function to start a server
- DelegationClient: Server-to-server delegation with token exchange

Configuration:
- AgentServiceConfig: Service configuration

Integrations:
- integrations.crewai: CrewAI tools for agent-to-agent delegation
"""

import warnings

# New organized structure
from .client import AgentClient, ServiceDiscovery
from .server import AgentServer, DelegationClient, create_agent_card_server, serve_agent
from .config import AgentServiceConfig

# Integrations (optional)
try:
    from .integrations import crewai
except ImportError:
    crewai = None

__all__ = [
    # Configuration
    "AgentServiceConfig",
    # Client
    "AgentClient",
    "ServiceDiscovery",
    # Server
    "AgentServer",
    "create_agent_card_server",
    "serve_agent",
    "DelegationClient",
    # Integrations
    "crewai",
    # Backward compatibility aliases (deprecated)
    "A2AServiceClient",
    "A2AServiceClientSync",
    "A2AServiceClientWithOAuth",
]


# =============================================================================
# Backward Compatibility Aliases
# =============================================================================
# These aliases maintain backward compatibility with existing code.
# They will be removed in a future major version.
# =============================================================================


def _deprecated(old_name: str, new_name: str, removal_version: str = "2.0.0"):
    """Issue deprecation warning."""
    warnings.warn(
        f"'{old_name}' is deprecated and will be removed in version {removal_version}. "
        f"Use '{new_name}' instead. See MIGRATION.md for details.",
        DeprecationWarning,
        stacklevel=3,
    )


class A2AServiceClientWithOAuth:
    """Deprecated: Use AgentClient instead.
    
    This class is deprecated and will be removed in version 2.0.0.
    Use keycardai.agents.client.AgentClient instead.
    
    Example migration:
        >>> # Old (deprecated)
        >>> from keycardai.agents import A2AServiceClientWithOAuth
        >>> client = A2AServiceClientWithOAuth(config)
        >>> 
        >>> # New (recommended)
        >>> from keycardai.agents import AgentClient
        >>> client = AgentClient(config)
    """
    
    def __init__(self, *args, **kwargs):
        _deprecated("A2AServiceClientWithOAuth", "keycardai.agents.client.AgentClient")
        from .client.oauth import AgentClient as _AgentClient
        self._client = _AgentClient(*args, **kwargs)
    
    def __getattr__(self, name):
        return getattr(self._client, name)
    
    async def __aenter__(self):
        await self._client.__aenter__()
        return self
    
    async def __aexit__(self, *args):
        await self._client.__aexit__(*args)


class A2AServiceClient:
    """Deprecated: Use DelegationClient instead.
    
    This class is deprecated and will be removed in version 2.0.0.
    Use keycardai.agents.server.DelegationClient instead.
    
    Example migration:
        >>> # Old (deprecated)
        >>> from keycardai.agents import A2AServiceClient
        >>> client = A2AServiceClient(config)
        >>> 
        >>> # New (recommended)
        >>> from keycardai.agents.server import DelegationClient
        >>> client = DelegationClient(config)
    """
    
    def __init__(self, *args, **kwargs):
        _deprecated("A2AServiceClient", "keycardai.agents.server.DelegationClient")
        from .server.delegation import DelegationClient as _DelegationClient
        self._client = _DelegationClient(*args, **kwargs)
    
    def __getattr__(self, name):
        return getattr(self._client, name)
    
    async def __aenter__(self):
        await self._client.__aenter__()
        return self
    
    async def __aexit__(self, *args):
        await self._client.__aexit__(*args)


class A2AServiceClientSync:
    """Deprecated: Use DelegationClientSync instead.
    
    This class is deprecated and will be removed in version 2.0.0.
    Use keycardai.agents.server.DelegationClientSync instead.
    
    Example migration:
        >>> # Old (deprecated)
        >>> from keycardai.agents import A2AServiceClient
        >>> client = A2AServiceClient(config)
        >>> 
        >>> # New (recommended)
        >>> from keycardai.agents.server import DelegationClientSync
        >>> client = DelegationClientSync(config)
    """
    
    def __init__(self, *args, **kwargs):
        _deprecated("A2AServiceClientSync", "keycardai.agents.server.DelegationClientSync")
        from .server.delegation import DelegationClientSync as _DelegationClientSync
        self._client = _DelegationClientSync(*args, **kwargs)
    
    def __getattr__(self, name):
        return getattr(self._client, name)
    
    def __enter__(self):
        self._client.__enter__()
        return self
    
    def __exit__(self, *args):
        self._client.__exit__(*args)
