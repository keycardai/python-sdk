"""The per-call AccessContext a tool reads with ``get_access_context()``."""

from __future__ import annotations

from contextvars import ContextVar

from keycardai.oauth.server import AccessContext

_current_access: ContextVar[AccessContext | None] = ContextVar(
    "keycard_hermes_access_context", default=None
)


def get_access_context() -> AccessContext:
    """The AccessContext for the tool call currently executing.

    Hermes copies the calling context into its tool worker threads, so a sync
    tool, a thread-pool tool and an async tool all see the context the
    middleware installed for their call. Raises RuntimeError when no
    middleware and no ``grant()`` / ``agrant()`` block installed one.
    """
    access = _current_access.get()
    if access is None:
        raise RuntimeError(
            "No Keycard AccessContext for this tool call. Enable the keycardai-hermes "
            "plugin for this profile, or wrap the call in keycardai.hermes.grant()."
        )
    return access
