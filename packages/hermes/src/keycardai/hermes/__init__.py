"""Keycard plugin for Hermes Agent: delegated, per-tool-call access.

    plugins:
      enabled: [keycardai-hermes]
      entries:
        keycardai-hermes:
          settings:
            zone_url: https://zone.keycard.cloud
            application_credential: {type: client_secret, client_id: ..., secret_name: KEYCARD_CLIENT_SECRET}
            sign_in_resource: urn:hermes:assistant
            owner_actor: alice
            tool_resources: {calendar_search: [https://calendar.example.com]}

A tool reads its credential with ``get_access_context().access(resource)``.
"""

from keycardai.oauth.server import Access, AccessContext, KeycardIdentity

from .access import get_access_context
from .plugin import KeycardPlugin, agrant, grant, register
from .settings import HermesConfigurationError, HermesSettings

__all__ = [
    "Access",
    "AccessContext",
    "KeycardIdentity",
    "KeycardPlugin",
    "HermesConfigurationError",
    "HermesSettings",
    "get_access_context",
    "grant",
    "agrant",
    "register",
]
