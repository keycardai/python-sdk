"""Deprecated re-export of :mod:`keycardai.fastmcp.provider`.

The implementation lives at ``keycardai.fastmcp.provider``. This module is
preserved so existing callers using
``from keycardai.mcp.integrations.fastmcp.provider import ...`` continue to
work; new code should import from ``keycardai.fastmcp.provider`` directly.

Tracks the canonical module's public surface via ``__all__`` so symbol
additions there flow through automatically.
"""

from keycardai.fastmcp.provider import *  # noqa: F401, F403
from keycardai.fastmcp.provider import __all__  # noqa: F401
