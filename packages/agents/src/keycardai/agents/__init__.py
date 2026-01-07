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

from .client import AgentClient, ServiceDiscovery
from .config import AgentServiceConfig
from .server import AgentServer, DelegationClient, create_agent_card_server, serve_agent

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
]
