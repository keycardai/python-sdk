"""Keycard integration for Pydantic AI agents.

One capability on the agent, one identity on the run's deps, one accessor
inside each tool:

    from pydantic_ai import Agent
    from keycardai.pydantic_ai import Access, KeycardGrant, KeycardIdentity, get_access_context

    keycard = KeycardGrant(zone_url=..., resources=[CALENDAR], client_id=..., client_secret=...)
    agent = Agent("openai:gpt-5", deps_type=KeycardIdentity, capabilities=[keycard])

    @agent.tool
    def list_events(ctx, days_ahead: int = 0) -> str:
        token = get_access_context().access(CALENDAR).access_token
        ...

    agent.run_sync("...", deps=Access.on_behalf_of(caller_token))
"""

from keycardai.oauth.server import Access, KeycardIdentity
from keycardai.oauth.server.access_context import AccessContext
from keycardai.oauth.server.exceptions import ResourceAccessError

from .grant import KeycardGrant, MCPAuthError, get_access_context, resume_results

__all__ = [
    "Access",
    "AccessContext",
    "KeycardGrant",
    "KeycardIdentity",
    "MCPAuthError",
    "ResourceAccessError",
    "get_access_context",
    "resume_results",
]
