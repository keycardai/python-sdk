"""Server primitives for building Keycard-protected A2A agent services.

Customers implement a2a-sdk's native ``AgentExecutor``
(``a2a.server.agent_execution.AgentExecutor``) and pass an instance to
``AgentServiceConfig``. This package wires the executor into a Starlette
app with Keycard authentication and the standard A2A JSONRPC endpoint;
no parallel protocol or custom request shape is introduced.

Exports:
- ``AgentServer``: high-level class wrapping the Starlette composition.
- ``create_agent_card_server``: factory function returning the configured Starlette app.
- ``serve_agent``: blocking uvicorn runner.
- ``DelegationClient`` / ``DelegationClientSync``: server-to-server token
  exchange helpers for calling other agent services on behalf of the
  original user.
"""

from .app import AgentServer, create_agent_card_server, serve_agent
from .delegation import DelegationClient, DelegationClientSync

__all__ = [
    "AgentServer",
    "create_agent_card_server",
    "serve_agent",
    "DelegationClient",
    "DelegationClientSync",
]
