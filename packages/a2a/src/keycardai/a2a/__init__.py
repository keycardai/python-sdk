"""KeycardAI A2A: Keycard-protected agent-to-agent delegation over a2a-sdk.

Wraps a2a-sdk's standard server primitives (``AgentExecutor``,
``DefaultRequestHandler``, the Starlette route factories) with Keycard
authentication and delegated token exchange. Customers implement
a2a-sdk's native async ``AgentExecutor`` and pass an instance to
``AgentServiceConfig``; this package handles the OAuth bearer
verification, OAuth metadata discovery endpoints, and Starlette
composition.

Server (build agent services):
- AgentServer: high-level server class
- create_agent_card_server: Starlette app factory with Keycard OAuth wiring
- serve_agent: blocking convenience runner
- DelegationClient / DelegationClientSync: server-to-server token exchange

Client (call agent services):
- ServiceDiscovery: query an agent service's `.well-known/agent-card.json`

Configuration:
- AgentServiceConfig: identity, credentials, executor, and capabilities
"""

from .client import ServiceDiscovery
from .config import AgentServiceConfig
from .server import (
    AgentServer,
    DelegationClient,
    DelegationClientSync,
    create_agent_card_server,
    serve_agent,
)

__all__ = [
    "AgentServiceConfig",
    "ServiceDiscovery",
    "AgentServer",
    "create_agent_card_server",
    "serve_agent",
    "DelegationClient",
    "DelegationClientSync",
]
