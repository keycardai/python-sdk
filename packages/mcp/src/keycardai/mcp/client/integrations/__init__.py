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
    from . import langchain_agents  # noqa: F401
    __all__.extend(["langchain_agents"])
except ImportError:
    pass

# Try to import OpenAI Agents integration
try:
    from . import openai_agents  # noqa: F401
    from .openai_agents import OpenAIMCPServer  # noqa: F401
    __all__.extend(["openai_agents", "OpenAIMCPServer"])
except ImportError:
    pass

# Try to import CrewAI integration
try:
    from . import crewai_agents  # noqa: F401
    __all__.extend(["crewai_agents"])
except ImportError:
    pass

# Try to import Pydantic AI integration
try:
    from . import pydantic_agents  # noqa: F401
    __all__.extend(["pydantic_agents"])
except ImportError:
    pass

