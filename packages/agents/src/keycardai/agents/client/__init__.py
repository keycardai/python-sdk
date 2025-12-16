"""Client package for calling agent services.

This package provides tools for users and applications to call agent services:
- AgentClient: User authentication with PKCE OAuth flow
- ServiceDiscovery: Discover and query agent service capabilities
"""

from .discovery import ServiceDiscovery
from .oauth import AgentClient

__all__ = [
    "AgentClient",
    "ServiceDiscovery",
]
