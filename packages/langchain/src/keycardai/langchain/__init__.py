"""Keycard integration for LangChain agents.

Adds delegated access at the tool-call boundary: every tool call gets a
short-lived credential brokered by Keycard, scoped to the identity the agent is
acting for, and audited as a delegation chain.

Quick start:

    from langchain.agents import create_agent
    from keycardai.langchain import (
        KeycardGrantMiddleware,
        KeycardIdentity,
        get_access_context,
    )

    keycard = KeycardGrantMiddleware(
        zone_url="https://your-zone.keycard.cloud",
        resources=["https://api.example.com"],
        client_id="your-app",
        client_secret=...,
    )

    @tool
    def call_api(query: str) -> str:
        \"\"\"Call the external API.\"\"\"
        token = get_access_context().access("https://api.example.com").access_token
        ...

    agent = create_agent(
        model,
        tools=[call_api],
        middleware=[keycard],
        context_schema=KeycardIdentity,
    )

    agent.invoke(
        {"messages": [...]},
        context=KeycardIdentity(subject_token=caller_token),
    )

Re-export guide:

- Local definitions: ``KeycardGrantMiddleware``, ``KeycardIdentity``,
  ``get_access_context``.
- Borrowed from ``keycardai-oauth``: ``AccessContext`` (the per-request token
  container) and ``ResourceAccessError`` (raised only by
  ``AccessContext.access``), re-exported so callers need one import.
"""

from keycardai.oauth.server.access_context import AccessContext
from keycardai.oauth.server.exceptions import ResourceAccessError

from .middleware import (
    KeycardGrantMiddleware,
    KeycardIdentity,
    get_access_context,
)

__all__ = [
    # === Primary API ===
    "KeycardGrantMiddleware",
    "KeycardIdentity",
    "get_access_context",
    # === Re-exported from keycardai-oauth ===
    "AccessContext",
    "ResourceAccessError",
]
