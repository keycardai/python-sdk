"""Server package for implementing agent services.

This package provides tools for building agent services:
- AgentServer: Create and run agent services with OAuth middleware
- DelegationClient: Server-to-server delegation with token exchange
- serve_agent: Convenience function to start a server
- create_agent_card_server: Create FastAPI app for agent service
- AgentExecutor: Protocol for framework-agnostic agent execution
- SimpleExecutor, LambdaExecutor: Simple executor implementations
- KeycardToA2AExecutorBridge: Bridge adapter for A2A JSONRPC support
"""

from .app import AgentServer, create_agent_card_server, serve_agent
from .delegation import DelegationClient, DelegationClientSync
from .executor import AgentExecutor, LambdaExecutor, SimpleExecutor
from .executor_bridge import KeycardToA2AExecutorBridge

__all__ = [
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
