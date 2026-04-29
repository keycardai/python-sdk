"""KeycardAI A2A: Keycard auth primitives for ``a2a-sdk`` agent services.

This package is glue, not a parallel server abstraction. Customers
implement a2a-sdk's native async ``AgentExecutor`` and compose a2a-sdk's
standard primitives (``DefaultRequestHandler``, ``create_jsonrpc_routes``,
``create_agent_card_routes``) into their own Starlette / FastAPI app.
This package contributes:

Server-side wiring:
- ``EagerKeycardAuthBackend``: an ``AuthenticationBackend`` that 401s on
  anonymous requests. Use inside Starlette's ``AuthenticationMiddleware``.
- ``KeycardServerCallContextBuilder``: a ``ServerCallContextBuilder`` that
  surfaces the verified bearer token on ``ServerCallContext.state``.
- ``build_agent_card_from_config``: construct a 1.x ``AgentCard``.

Outbound delegation:
- ``DelegationClient`` / ``DelegationClientSync``: server-to-server token
  exchange and JSONRPC invocation against another agent service.

Inbound discovery:
- ``ServiceDiscovery``: query a remote agent service's
  ``.well-known/agent-card.json`` with caching.

Configuration:
- ``AgentServiceConfig``: service identity + Keycard credentials + agent
  card metadata.

For a runnable composed server, see
``packages/a2a/examples/keycard_protected_server/``.
"""

from .client import ServiceDiscovery
from .config import AgentServiceConfig
from .server import (
    DelegationClient,
    DelegationClientSync,
    EagerKeycardAuthBackend,
    KeycardServerCallContextBuilder,
    build_agent_card_from_config,
)

__all__ = [
    "AgentServiceConfig",
    "ServiceDiscovery",
    "DelegationClient",
    "DelegationClientSync",
    "EagerKeycardAuthBackend",
    "KeycardServerCallContextBuilder",
    "build_agent_card_from_config",
]
