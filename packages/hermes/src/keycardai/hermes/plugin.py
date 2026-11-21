"""``register(ctx)``: the Hermes plugin entry point, and the ``grant`` escape hatch."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

from keycardai.oauth import AsyncClient
from keycardai.oauth.server import AccessContext, AgentGrantEngine, KeycardIdentity

from .access import _current_access
from .grants import Grants, resolve_actor, sign_in_message
from .middleware import (
    KeycardMiddleware,
    SessionEnv,
    ToolsetOf,
    default_session_env,
    default_toolset_of,
)
from .secret_source import KeycardSecretSource
from .settings import (
    PLUGIN_ID,
    HermesConfigurationError,
    HermesSettings,
    build_credential,
    load_settings,
)
from .store import SignInStore

ISOLATION_HOST = "host"
# Hermes sets this in the plugin host child (hermes_cli/plugin_isolation.py:98),
# where isolation_mode() answers in_process, so the child is detected by it.
HOST_PROCESS_ENV = "HERMES_PLUGIN_HOST_PROCESS"
MISSING_IDENTITY_MESSAGE = "No Keycard sign-in for this actor. Run `hermes keycard login --actor <actor>`."


def default_get_secret(name: str) -> str | None:
    """Hermes's profile-scoped ``get_secret``, else the environment."""
    try:
        get_secret = importlib.import_module("agent.secret_scope").get_secret
    except ImportError:
        return os.environ.get(name)
    value = get_secret(name)
    return str(value) if value else None


def default_isolation_mode() -> str:
    """Hermes's ``isolation_mode()``; inside the plugin host process itself
    (``HERMES_PLUGIN_HOST_PROCESS=1``) it reports in-process, so the host
    marker is checked first."""
    if os.environ.get(HOST_PROCESS_ENV) == "1":
        return ISOLATION_HOST
    try:
        isolation_mode = importlib.import_module("hermes_cli.plugin_isolation").isolation_mode
    except ImportError:
        return "inline"
    return str(isolation_mode())


def default_hermes_home() -> Path:
    try:
        get_hermes_home = importlib.import_module("hermes_constants").get_hermes_home
    except ImportError:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return Path(get_hermes_home())


def build_grants(
    settings: HermesSettings,
    *,
    home: Path,
    get_secret: Callable[[str], str | None] = default_get_secret,
    client: AsyncClient | None = None,
) -> Grants:
    credential = build_credential(settings, get_secret)
    engine = AgentGrantEngine(
        zone_url=settings.zone_url,
        resources=settings.resources,
        application_credential=credential,
        tool_resources=settings.tool_resources,
        request_scopes=settings.request_scopes,
        authorization_url=settings.authorization_url,
        missing_identity_message=MISSING_IDENTITY_MESSAGE,
        client=client,
    )
    return Grants(settings, engine, SignInStore(home, settings.zone_url), public_client=client)


class KeycardPlugin:
    """What ``register`` builds for one profile: the grants, the middleware,
    the secret source and the CLI handlers."""

    def __init__(
        self,
        grants: Grants,
        *,
        session_env: SessionEnv = default_session_env,
        toolset_of: ToolsetOf = default_toolset_of,
        home: Path | None = None,
    ) -> None:
        self.grants = grants
        self.settings = grants.settings
        self.home = home if home is not None else grants.store.home
        self.middleware = KeycardMiddleware(grants, session_env=session_env, toolset_of=toolset_of)
        self.secret_source = KeycardSecretSource(grants)

    def post_gateway_admission(self, **context: Any) -> dict[str, str] | None:
        """Answer a message from an actor with no sign-in with the login
        instruction, consuming it before the turn starts. Hermes consumes
        a ``{"action": "handled", "reply": ...}`` result
        (gateway/run_inbound_consumer.py:53-58); None lets the turn run."""
        if self.settings.default_pattern != "on_behalf_of":
            return None
        source = context.get("source")
        raw = source.get("user_id") if isinstance(source, dict) else getattr(source, "user_id", None)
        sender = str(raw or "")
        if not sender:
            # No sender to check: let the turn run and the middleware decide,
            # rather than admitting an unknown sender as owner_actor.
            return None
        actor = resolve_actor(self.settings, sender)
        if actor is None:
            return {"action": "handled", "reply": sign_in_message("missing_identity", None)}
        if self.grants.store.load(actor) is None:
            return {"action": "handled", "reply": sign_in_message("missing_identity", actor)}
        return None

    def setup_cli(self, parser: argparse.ArgumentParser) -> None:
        from .cli import setup_parser

        setup_parser(parser)

    def handle_cli(self, args: argparse.Namespace) -> int:
        from .cli import handle

        return handle(self, args)


# One plugin per Hermes home: a multiplexed gateway registers each profile in
# one process, so the escape hatch looks its profile up rather than taking the
# last registration.
_registered: dict[Path, KeycardPlugin] = {}


def _inside_hermes() -> bool:
    return importlib.util.find_spec("hermes_constants") is not None


def current_plugin(
    home: Callable[[], Path] | None = None,
    inside_hermes: Callable[[], bool] | None = None,
) -> KeycardPlugin:
    """The plugin registered for the active Hermes home. Inside Hermes the
    lookup is by home only, so a profile without the plugin never gets another
    profile's. Outside Hermes (tests, scripts) a single registration is used
    whatever the home resolves to."""
    home = home or default_hermes_home
    inside_hermes = inside_hermes or _inside_hermes
    plugin = _registered.get(Path(home()).resolve())
    if plugin is None and len(_registered) == 1 and not inside_hermes():
        plugin = next(iter(_registered.values()))
    if plugin is None:
        raise RuntimeError(
            "keycardai-hermes is not registered for this profile: enable the plugin "
            "for it, or build a KeycardPlugin and pass it to grant()."
        )
    return plugin


def register(
    ctx: Any,
    *,
    isolation_mode: Callable[[], str] = default_isolation_mode,
    get_secret: Callable[[str], str | None] = default_get_secret,
    hermes_home: Callable[[], Path] = default_hermes_home,
    session_env: SessionEnv = default_session_env,
    toolset_of: ToolsetOf = default_toolset_of,
    client: AsyncClient | None = None,
    admission_hook: bool = True,
) -> KeycardPlugin:
    """The ``hermes_agent.plugins`` entry point. Hermes calls ``register(ctx)``;
    the keyword arguments are seams for tests.

    Raises ``HermesConfigurationError`` on a misconfiguration or a load under
    ``plugins.isolation: host``; Hermes then marks the plugin failed and
    unwinds whatever it registered.
    """
    if isolation_mode() == ISOLATION_HOST:
        raise HermesConfigurationError(
            "plugins.isolation",
            "host is not supported: a ContextVar set by the middleware cannot reach "
            "a tool from the plugin host process",
            absolute=True,
        )
    settings = load_settings(ctx.get_config)
    home = Path(hermes_home()).resolve()
    grants = build_grants(settings, home=home, get_secret=get_secret, client=client)
    plugin = KeycardPlugin(grants, session_env=session_env, toolset_of=toolset_of, home=home)
    ctx.register_middleware("tool_execution", plugin.middleware)
    ctx.register_secret_source(plugin.secret_source)
    ctx.register_cli_command(
        "keycard",
        "Keycard sign-in and tokens for this profile",
        plugin.setup_cli,
        plugin.handle_cli,
    )
    if (
        admission_hook
        and settings.sign_in_on_admission
        and settings.default_pattern == "on_behalf_of"
        and hasattr(ctx, "register_hook")
    ):
        ctx.register_hook("post_gateway_admission", plugin.post_gateway_admission)
    _registered[home] = plugin
    return plugin


@contextmanager
def grant(
    identity: KeycardIdentity | None = None,
    *,
    tool_name: str | None = None,
    resources: list[str] | None = None,
    plugin: KeycardPlugin | None = None,
) -> Iterator[AccessContext]:
    """Serve ``get_access_context()`` outside a tool call: the same context the
    middleware would install. ``identity`` defaults to ``owner_actor`` under
    ``default_pattern``."""
    plugin = plugin or current_plugin()
    engine = plugin.grants.engine
    targets = _targets(plugin, tool_name, resources)
    if identity is None:
        access, _ = plugin.grants.acquire(resolve_actor(plugin.settings, None), targets)
    else:
        access = engine.run_sync(engine.build_access(identity, targets))
    token = _current_access.set(access)
    try:
        yield access
    finally:
        _current_access.reset(token)


@asynccontextmanager
async def agrant(
    identity: KeycardIdentity | None = None,
    *,
    tool_name: str | None = None,
    resources: list[str] | None = None,
    plugin: KeycardPlugin | None = None,
) -> AsyncIterator[AccessContext]:
    """Async ``grant()``: the same contract on the running event loop."""
    plugin = plugin or current_plugin()
    engine = plugin.grants.engine
    targets = _targets(plugin, tool_name, resources)
    if identity is None:
        access = await plugin.grants.aacquire(resolve_actor(plugin.settings, None), targets)
    else:
        access = await engine.build_access(identity, targets)
    token = _current_access.set(access)
    try:
        yield access
    finally:
        _current_access.reset(token)


def _targets(plugin: KeycardPlugin, tool_name: str | None, resources: list[str] | None) -> list[str]:
    if tool_name is not None and resources is not None:
        raise ValueError("Pass tool_name or resources, not both")
    if resources is not None:
        return list(resources)
    if tool_name is not None:
        return plugin.grants.engine.resources_for(tool_name)
    return plugin.settings.resources


__all__ = ["KeycardPlugin", "PLUGIN_ID", "register", "grant", "agrant", "build_grants"]
