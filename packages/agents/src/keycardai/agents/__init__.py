"""KeycardAI Agents (legacy package).

This package previously housed three concerns. Per the KEP "Decompose
keycardai-agents", they have moved to:

- A2A delegation, agent service hosting, executor primitives, and service
  discovery → ``keycardai-a2a`` (``from keycardai.a2a import ...``).
- OAuth 2.0 PKCE user-login flow (``AgentClient``) → ``keycardai-oauth``
  (``from keycardai.oauth.pkce import authenticate``).
- The CrewAI-over-A2A integration is the only remaining piece, accessible
  via ``from keycardai.agents.integrations.crewai import ...``. It will
  move to a dedicated ``keycardai-crewai`` package; this package will be
  archived once that ships.
"""

# Integrations (optional)
try:
    from .integrations import crewai
except ImportError:
    crewai = None

__all__ = [
    "crewai",
]
