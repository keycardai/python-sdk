"""Integrations for various agent frameworks.

This module provides integrations for popular agent frameworks like CrewAI,
enabling seamless A2A delegation and service orchestration.

Available integrations:
- crewai_a2a: CrewAI integration with automatic A2A tool generation
"""

from .crewai_a2a import get_a2a_tools, create_a2a_tool_for_service

__all__ = ["get_a2a_tools", "create_a2a_tool_for_service"]
