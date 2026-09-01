"""Keycard integration for LangChain agents.

Adds delegated access at the tool-call boundary: every tool call gets a
short-lived credential brokered by Keycard, scoped to the identity the agent is
acting for, and audited as a delegation chain.

Quick start:

    from langchain.agents import create_agent
    from keycardai.langchain import (
        Access,
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
        context=Access.on_behalf_of(caller_token),
    )

Re-export guide:

- Local definitions: ``Access``, ``Caller``, ``KeycardGrantMiddleware``,
  ``KeycardIdentity``, ``caller_from_config``, ``get_access_context``.
  ``KeycardIdentity`` is the context schema, and can also be constructed
  directly.
- Inbound authentication for a served agent lives in
  ``keycardai.langchain.auth`` (the ``serve`` extra), imported from there so
  the package root stays free of ``langgraph-sdk`` and ``starlette``.
- Borrowed from ``keycardai-oauth``: ``AccessContext`` (the per-request token
  container) and ``ResourceAccessError`` (raised only by
  ``AccessContext.access``), re-exported so callers need one import.
"""

from keycardai.oauth.server.access_context import AccessContext
from keycardai.oauth.server.exceptions import ResourceAccessError

from .access import Access
from .middleware import (
    Caller,
    KeycardGrantMiddleware,
    KeycardIdentity,
    caller_from_config,
    get_access_context,
)

__all__ = [
    # === Primary API ===
    "Access",
    "Caller",
    "KeycardGrantMiddleware",
    "KeycardIdentity",
    "caller_from_config",
    "get_access_context",
    # === Re-exported from keycardai-oauth ===
    "AccessContext",
    "ResourceAccessError",
]
