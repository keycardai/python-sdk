"""The ``tool_execution`` middleware: the grant at the tool-call boundary."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from typing import Any

from keycardai.oauth.server import AccessContext

from .access import _current_access
from .grants import Grants, resolve_actor, sign_in_text

ACTOR_VAR = "HERMES_SESSION_ACTOR_ID"
SessionEnv = Callable[[str, str], str]
ToolsetOf = Callable[[str], str | None]


def default_session_env(name: str, default: str = "") -> str:
    """Hermes's ``get_session_env``: the turn's ContextVar, else the environment."""
    try:
        get_session_env = importlib.import_module("gateway.session_context").get_session_env
    except ImportError:
        return os.environ.get(name, default)
    return str(get_session_env(name, default))


def default_toolset_of(tool_name: str) -> str | None:
    """The toolset ``tools.registry`` records for a tool, or None outside Hermes."""
    try:
        registry = importlib.import_module("tools.registry").registry
    except ImportError:
        return None
    toolset = registry.get_toolset_for_tool(tool_name)
    return str(toolset) if toolset else None


def is_mcp_toolset(toolset: str | None) -> bool:
    return bool(toolset) and str(toolset).startswith("mcp-")


class _ToolRaised(Exception):
    """Carries a tool's own exception past the middleware's catch-all, so Hermes
    sees the tool fail exactly as it would without the plugin."""

    def __init__(self, original: BaseException) -> None:
        super().__init__(str(original))
        self.original = original


class KeycardMiddleware:
    """Synchronous, and it never raises: every failure ends in a tool result.

    A blocked call returns its result without calling ``next_call``. A call
    that runs gets an AccessContext in a ContextVar for the duration of
    ``next_call``, reset in ``finally``.
    """

    def __init__(
        self,
        grants: Grants,
        *,
        session_env: SessionEnv = default_session_env,
        toolset_of: ToolsetOf = default_toolset_of,
    ) -> None:
        self.grants = grants
        self.settings = grants.settings
        self.engine = grants.engine
        self._session_env = session_env
        self._toolset_of = toolset_of

    def __call__(
        self, tool_name: str, args: dict[str, Any], next_call: Callable[..., Any], **context: Any
    ) -> Any:
        try:
            return self._run(tool_name, args, next_call)
        except _ToolRaised as raised:
            raise raised.original from None
        except Exception as exc:
            return (
                f"keycard: the tool {tool_name} could not be granted access "
                f"({type(exc).__name__}). Nothing was run."
            )

    def resolve_actor(self) -> str | None:
        bound = self._session_env(ACTOR_VAR, "")
        return resolve_actor(self.settings, bound)

    def resources_for(self, tool_name: str) -> list[str]:
        if is_mcp_toolset(self._toolset_of(tool_name)):
            return []
        return self.engine.resources_for(tool_name)

    def _run(self, tool_name: str, args: dict[str, Any], next_call: Callable[..., Any]) -> Any:
        resources = self.resources_for(tool_name)
        if not resources:
            return self._with_context(AccessContext(), args, next_call)
        pattern = self.settings.default_pattern
        actor = self.resolve_actor()
        access, needed = self.grants.acquire(actor, resources, pattern)
        if needed is not None and self.settings.block_without_sign_in:
            return sign_in_text(needed.reason, needed.actor, tool_name)
        failed = access.get_failed_resources()
        if failed and pattern != "as_self" and self.settings.authorization_url:
            payload = self.engine.authorization_payload(failed, access)
            return self.engine.fallback_text(self.engine.fallback_fields(payload, tool_name))
        return self._with_context(access, args, next_call)

    @staticmethod
    def _with_context(
        access: AccessContext, args: dict[str, Any], next_call: Callable[..., Any]
    ) -> Any:
        token = _current_access.set(access)
        try:
            return next_call(args)
        except Exception as exc:
            raise _ToolRaised(exc) from exc
        finally:
            _current_access.reset(token)
