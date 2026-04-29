"""Server-side primitives for Keycard-protected A2A agent services.

This package is glue, not a parallel server abstraction. Compose these
primitives with a2a-sdk's standard route factories and request handler
in your own Starlette/FastAPI app. See
``packages/a2a/examples/keycard_protected_server/`` for a runnable
composition.

Auth wiring:
- For the JSONRPC mount, use
  ``KeycardAuthBackend(verifier, require_authentication=True)`` from
  keycardai-starlette inside ``AuthenticationMiddleware``.
- ``KeycardServerCallContextBuilder``: ``ServerCallContextBuilder`` that
  exposes the verified ``KeycardUser`` and bare ``access_token`` on
  ``ServerCallContext.state``. Pass to ``create_jsonrpc_routes``.

Agent card:
- ``build_agent_card_from_config``: construct an ``a2a.types.AgentCard``
  protobuf from an ``AgentServiceConfig``. Pass to
  ``create_agent_card_routes`` and ``DefaultRequestHandler``.

Outbound delegation:
- ``DelegationClient`` / ``DelegationClientSync``: server-to-server token
  exchange and A2A JSONRPC invocation against another agent service.
"""

from .app import (
    KeycardServerCallContextBuilder,
    build_agent_card_from_config,
)
from .delegation import DelegationClient, DelegationClientSync

__all__ = [
    "KeycardServerCallContextBuilder",
    "build_agent_card_from_config",
    "DelegationClient",
    "DelegationClientSync",
]
