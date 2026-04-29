"""KeycardAI Agents (legacy package, pending archive).

This package previously housed three concerns. Per the KEP "Decompose
keycardai-agents", they have all moved:

- A2A delegation, agent service primitives, and service discovery →
  ``keycardai-a2a`` (``from keycardai.a2a import ...``).
- OAuth 2.0 PKCE user-login flow (``AgentClient``) → ``keycardai-oauth``
  (``from keycardai.oauth.pkce import authenticate``).
- CrewAI-over-A2A integration (executor + delegation tools) →
  ``keycardai-crewai`` (``from keycardai.crewai import ...``).

This package now exposes no symbols. It will be archived once downstream
references catch up (tracked in ACC-232).
"""
