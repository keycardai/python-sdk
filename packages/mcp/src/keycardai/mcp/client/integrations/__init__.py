"""MCP client integrations for agent frameworks."""

# Import auth tools (always available)
from .auth_tools import (
    AuthToolHandler,
    ConsoleAuthToolHandler,
    DefaultAuthToolHandler,
    SlackAuthToolHandler,
)

# Optional integration imports - only available if dependencies are installed
__all__ = [
    # Auth tool handlers (always available)
    "AuthToolHandler",
    "DefaultAuthToolHandler",
    "SlackAuthToolHandler",
    "ConsoleAuthToolHandler",
]

# Try to import LangChain integration
try:
    from . import langchain_agents
    __all__.extend(["langchain_agents"])
except ImportError:
    pass

# Try to import OpenAI Agents integration
try:
    from . import openai_agents
    from .openai_agents import OpenAIMCPServer
    __all__.extend(["openai_agents", "OpenAIMCPServer"])
except ImportError:
    pass

