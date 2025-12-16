"""Integrations with agent frameworks.

This package provides integrations with various agent frameworks:
- CrewAI: Tools for agent-to-agent delegation
"""

try:
    from .crewai import get_a2a_tools, set_delegation_token, create_a2a_tool_for_service
    
    __all__ = [
        "get_a2a_tools",
        "set_delegation_token",
        "create_a2a_tool_for_service",
    ]
except ImportError:
    # CrewAI not installed
    __all__ = []
