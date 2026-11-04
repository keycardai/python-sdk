"""Keycard grant capability for Pydantic AI agents.

Grants delegated access at the tool-call boundary: before each tool executes,
the capability acquires a credential for the identity the run acts under
through the shared engine in keycardai-oauth, and exposes the result to the
tool as a non-throwing AccessContext. Human flows (sign-in, consent) use the
framework's own deferred-tool approval.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any

from keycardai.oauth import AsyncClient
from keycardai.oauth.server import (
    AgentGrantEngine,
    KeycardIdentity,
    RequestScopes,
    ToolResources,
)
from keycardai.oauth.server.access_context import AccessContext
from keycardai.oauth.server.credentials import ApplicationCredential
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ApprovalRequired, ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

MISSING_IDENTITY_MESSAGE = (
    "No Keycard identity on the run's deps. Run the agent with "
    "deps=Access.on_behalf_of(...), Access.impersonate(...), or Access.as_self(), "
    "or carry a KeycardIdentity on deps.keycard_identity."
)

_current_access: ContextVar[AccessContext | None] = ContextVar(
    "keycard_pydantic_ai_access_context", default=None
)


def get_access_context() -> AccessContext:
    """The AccessContext for the tool call currently executing.

    Call from inside a tool, sync or async: the framework copies the context
    into a sync tool's worker thread. Raises RuntimeError when no KeycardGrant
    and no grant() / agrant() block installed one.
    """
    access = _current_access.get()
    if access is None:
        raise RuntimeError(
            "No Keycard AccessContext for this tool call. Add KeycardGrant to the "
            "agent's capabilities and run the agent with an Access.* identity as "
            "deps, or wrap the call in keycard.grant()."
        )
    return access


def default_identity_from(ctx: RunContext[Any]) -> KeycardIdentity | None:
    """Identity from the run's deps: the deps object itself, or its
    `keycard_identity` attribute. Never `RunContext.metadata`."""
    deps = ctx.deps
    if isinstance(deps, KeycardIdentity):
        return deps
    identity = getattr(deps, "keycard_identity", None)
    return identity if isinstance(identity, KeycardIdentity) else None


def _durability_capability(ctx: RunContext[Any]) -> object | None:
    """The durability capability bound to the run's agent, if any.

    Checked on the agent rather than `ctx.in_durable_context`: that flag is
    false inside a Temporal activity or DBOS step, which is where sibling
    seams run, so a flag-only check would let a token into history.
    """
    if ctx.agent is None:
        return None
    try:
        from pydantic_ai.durable_exec import BaseDurabilityCapability
    except ImportError:
        # Every durability capability subclasses this type, so none can exist.
        return None
    return BaseDurabilityCapability.from_agent(ctx.agent)


class KeycardGrant(AbstractCapability[Any]):
    """Acquire a credential for every tool call, under the run's identity.

    Add it to the agent's capabilities; identity travels on the run's deps.
    Constructor parameters are keyword-only and documented in the package
    README; `defer_on_auth` chooses between a deferred-tool pause and a
    terminal `ToolFailed` for sign-in and consent, `identity_from` replaces
    the default deps lookup, and `fallback_identity` applies when it finds
    none. The capability holds configuration only, never per-run state.
    """

    def __init__(
        self,
        *,
        zone_url: str | None = None,
        resources: list[str],
        application_credential: ApplicationCredential | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        tool_resources: ToolResources | None = None,
        request_scopes: RequestScopes | None = None,
        authorization_url: str | Callable[[list[str]], str] | None = None,
        sign_in_url: str | None = None,
        defer_on_auth: bool = True,
        identity_from: Callable[[RunContext[Any]], KeycardIdentity | None]
        | None = None,
        fallback_identity: KeycardIdentity
        | Callable[[], KeycardIdentity | None]
        | None = None,
        client: AsyncClient | None = None,
    ) -> None:
        super().__init__()
        self._engine = AgentGrantEngine(
            zone_url=zone_url,
            resources=resources,
            application_credential=application_credential,
            client_id=client_id,
            client_secret=client_secret,
            tool_resources=tool_resources,
            request_scopes=request_scopes,
            authorization_url=authorization_url,
            sign_in_url=sign_in_url,
            fallback_identity=fallback_identity,
            missing_identity_message=MISSING_IDENTITY_MESSAGE,
            client=client,
        )
        self._defer_on_auth = defer_on_auth
        self._identity_from = identity_from or default_identity_from

    @property
    def engine(self) -> AgentGrantEngine:
        """The shared acquisition engine this capability drives."""
        return self._engine

    def resolve_identity(self, ctx: RunContext[Any]) -> KeycardIdentity | None:
        """The identity for this tool call: `identity_from`, then the fallback."""
        identity = self._identity_from(ctx)
        return identity if identity is not None else self._engine.resolve_fallback()

    def _check_durable(self, ctx: RunContext[Any]) -> None:
        durability = _durability_capability(ctx)
        if durability is not None:
            raise RuntimeError(
                "KeycardGrant does not support durable execution yet: under "
                f"{type(durability).__name__} capability hooks run inside the "
                "durable container and deps are serialized into its history, so a "
                "token acquired here would be replayed and a subject token "
                "persisted. Run the agent without a durability capability."
            )

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: Callable[[dict[str, Any]], Any],
    ) -> Any:
        self._check_durable(ctx)
        resources = self._engine.resources_for(tool_def.name)
        identity = self.resolve_identity(ctx)
        access = await self._engine.build_access(identity, resources)
        payload = self._engine.pending_pause(identity, access)
        if payload is not None:
            if self._defer_on_auth and not ctx.tool_call_approved:
                raise ApprovalRequired(metadata=payload)
            fields = self._engine.fallback_fields(payload, tool_def.name)
            raise ToolFailed(self._engine.fallback_text(fields))
        token = _current_access.set(access)
        try:
            return await handler(args)
        finally:
            _current_access.reset(token)

    @contextmanager
    def grant(
        self,
        identity: KeycardIdentity | None = None,
        *,
        tool_name: str | None = None,
        resources: list[str] | None = None,
    ) -> Iterator[AccessContext]:
        """Serve get_access_context() for code that runs outside an agent run.

        `identity` defaults to `fallback_identity`; `tool_name` applies that
        tool's override, `resources` grants exactly the listed resources (the
        two are mutually exclusive), and with neither the default resources
        are granted. There is no run to pause, so nothing defers here:
        failures stay on the yielded AccessContext, exactly as tools see them.
        """
        resolved, targets = self._engine.grant_target(identity, tool_name, resources)
        access = self._engine.run_sync(self._engine.build_access(resolved, targets))
        token = _current_access.set(access)
        try:
            yield access
        finally:
            _current_access.reset(token)

    @asynccontextmanager
    async def agrant(
        self,
        identity: KeycardIdentity | None = None,
        *,
        tool_name: str | None = None,
        resources: list[str] | None = None,
    ) -> AsyncIterator[AccessContext]:
        """Async grant(): the same contract on the running event loop."""
        resolved, targets = self._engine.grant_target(identity, tool_name, resources)
        access = await self._engine.build_access(resolved, targets)
        token = _current_access.set(access)
        try:
            yield access
        finally:
            _current_access.reset(token)
