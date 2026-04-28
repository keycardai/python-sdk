"""KeycardAI A2A: agent-to-agent delegation with Keycard authentication.

Build agent services that can be called by other agents while preserving the
original user's identity and authorization context through OAuth 2.0 token
exchange (RFC 8693).

Server (build agent services):
- AgentServer: high-level server class
- create_agent_card_server: FastAPI app factory with Keycard OAuth wiring
- serve_agent: blocking convenience runner
- DelegationClient / DelegationClientSync: server-to-server token exchange

Executors:
- AgentExecutor: protocol for the per-request unit of work
- SimpleExecutor, LambdaExecutor: ergonomic executor implementations
- KeycardToA2AExecutorBridge: adapt a Keycard executor to the A2A SDK

Client (call agent services):
- ServiceDiscovery: query an agent service's `.well-known/agent-card.json`

Configuration:
- AgentServiceConfig: identity, credentials, executor, and capabilities
"""

from .client import ServiceDiscovery
from .config import AgentServiceConfig
from .server import (
    AgentExecutor,
    AgentServer,
    DelegationClient,
    DelegationClientSync,
    KeycardToA2AExecutorBridge,
    LambdaExecutor,
    SimpleExecutor,
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
    "AgentExecutor",
    "SimpleExecutor",
    "LambdaExecutor",
    "KeycardToA2AExecutorBridge",
]
