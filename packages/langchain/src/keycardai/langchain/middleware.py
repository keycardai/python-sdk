"""Keycard grant middleware for LangChain 1.x agents.

Grants delegated access at the tool-call boundary: before each tool executes,
the middleware exchanges the caller's identity for short-lived resource tokens
(RFC 8693) via the shared keycardai-oauth orchestration, and exposes the result
to the tool as a non-throwing AccessContext.

The same middleware instance works under `create_agent` and `create_deep_agent`
(deepagents is built on the create_agent middleware system).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import ToolMessage
from langgraph.types import Command, interrupt

from keycardai.oauth import AsyncClient
from keycardai.oauth.server.access_context import AccessContext
from keycardai.oauth.server.agent_grant import AgentGrantEngine, KeycardIdentity
from keycardai.oauth.server.credentials import ApplicationCredential
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest

AUTH_USER_KEY = "langgraph_auth_user"
SUBJECT_TOKEN_FIELD = "subject_token"
OWNER_KEY = "owner"


@dataclass(frozen=True)
class Caller:
    """A verified caller of a served run: who they are, and the bearer they sent."""

    identity: str
    subject_token: str


def _auth_user_field(user: Any, name: str) -> Any:
    """Read one field off the served user, which is mapping-like and attribute-like."""
    get = getattr(user, "get", None)
    if callable(get):
        value = get(name)
        if value is not None:
            return value
    return getattr(user, name, None)


def caller_from_config(config: Any) -> Caller | None:
    """The caller whose bearer authenticated this run, or None if there is none.

    A LangGraph server that authenticates requests (see
    `keycardai.langchain.auth`) delivers the verified user to the run as
    `config["configurable"]["langgraph_auth_user"]`. This is the read side of
    that channel: request-scoped, written only by the server, so two callers on
    one deployment never see each other.
    """
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable") or {}
    user = configurable.get(AUTH_USER_KEY)
    if user is None:
        return None
    identity = _auth_user_field(user, "identity")
    subject_token = _auth_user_field(user, SUBJECT_TOKEN_FIELD)
    if not identity or not subject_token:
        return None
    return Caller(identity=str(identity), subject_token=str(subject_token))


_current_access: ContextVar[AccessContext | None] = ContextVar(
    "keycard_access_context", default=None
)


def get_access_context() -> AccessContext:
    """The AccessContext for the tool call currently executing.

    Call from inside a tool. Raises RuntimeError when no KeycardGrantMiddleware
    wrapped this call.
    """
    access = _current_access.get()
    if access is None:
        raise RuntimeError(
            "No Keycard AccessContext for this tool call. Add KeycardGrantMiddleware "
            "to the agent's middleware list and invoke the agent with an "
            "Access.* identity as context."
        )
    return access


class KeycardGrantMiddleware(AgentMiddleware):
    """Exchange the caller's identity for resource tokens on every tool call.

    Args:
        zone_url: Keycard zone URL (issuer). Required unless `client` is given.
        resources: Resource URLs to grant for every tool call.
        application_credential: How the agent authenticates to the zone.
            `ClientSecret` for Keycard-issued client credentials,
            `WorkloadIdentity` for a platform-signed OIDC token (no static
            secret on the box). Mutually exclusive with client_id /
            client_secret.
        client_id / client_secret: Shorthand for
            `application_credential=ClientSecret((client_id, client_secret))`.
        tool_resources: Optional per-tool override, tool name -> resource URLs.
            Tools absent from the map get `resources`.
        request_scopes: Optional outbound scopes for the exchange, same shapes
            as the core orchestrator (str | list | dict per resource).
        authorization_url: When set, a failed exchange pauses the run with a
            LangGraph interrupt instead of recording a silent error. The
            interrupt payload carries this URL (str, or callable taking the
            failed resource URLs) for the user to establish the grant; on
            resume the exchange is retried. Requires a checkpointer unless
            `interrupt_on_auth=False`.
        sign_in_url: When set, a run that carries no identity, or whose
            subject token has already expired, pauses with a
            `sign_in_required` interrupt linking here, instead of failing.
            The payload's `reason` field says which case it was. The whole
            flow then lives in the chat: sign in, resume.
        identity_source: Where the identity for a tool call comes from.
            `"context"` (default) reads the agent's runtime context, which the
            caller supplies per invocation. `"auth_user"` reads the verified
            caller the LangGraph server put on the run config, for an agent
            served behind `keycardai.langchain.auth`: identity and subject
            token then come only from a verified bearer, never from the
            request body, and a run with no verified caller resolves to no
            identity. Mutually exclusive with fallback_identity.
        fallback_identity: Identity used when the runtime context carries
            none. Pass a callable to resolve it per tool call, so a sign-in
            that happens mid-run is picked up on resume without a restart.
        interrupt_on_auth: How an unmet authorization requirement reaches the
            user. True (default) pauses the run with a LangGraph interrupt,
            which requires a checkpointer. False sends the same payload to the
            model as failed tool output instead, so the model relays the URL in
            its reply; the user authorizes out of band and the next user turn
            retries the tool. No checkpointer involved, and no in-run resume.
        client: Injectable AsyncClient (tests). When set, zone_url is unused
            and the client is reused as-is.
    """

    def __init__(
        self,
        *,
        zone_url: str | None = None,
        resources: list[str],
        application_credential: ApplicationCredential | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        tool_resources: dict[str, list[str]] | None = None,
        request_scopes: str | list[str] | dict[str, str | list[str]] | None = None,
        authorization_url: str | Callable[[list[str]], str] | None = None,
        sign_in_url: str | None = None,
        identity_source: Literal["context", "auth_user"] = "context",
        fallback_identity: KeycardIdentity
        | Callable[[], KeycardIdentity | None]
        | None = None,
        interrupt_on_auth: bool = True,
        client: AsyncClient | None = None,
    ) -> None:
        super().__init__()
        if identity_source not in ("context", "auth_user"):
            raise ValueError(
                "identity_source must be 'context' or 'auth_user', "
                f"got {identity_source!r}"
            )
        if identity_source == "auth_user" and fallback_identity is not None:
            raise ValueError(
                "identity_source='auth_user' takes its identity from the verified "
                "caller on the run config, so fallback_identity would be a second, "
                "unverified source. Pass one or the other."
            )
        self._identity_source = identity_source
        self._tool_resources = tool_resources or {}
        self._interrupt_on_auth = interrupt_on_auth
        self._engine = AgentGrantEngine(
            zone_url=zone_url,
            resources=resources,
            application_credential=application_credential,
            client_id=client_id,
            client_secret=client_secret,
            tool_resources=self._tool_resources,
            request_scopes=request_scopes,
            authorization_url=authorization_url,
            sign_in_url=sign_in_url,
            fallback_identity=fallback_identity,
            client=client,
        )

    @property
    def _credential(self) -> ApplicationCredential | None:
        """The credential the engine authenticates with (kept for callers and tests)."""
        return self._engine.credential

    def _run_sync(self, coro: Coroutine[Any, Any, AccessContext]) -> AccessContext:
        """Run `coro` on the engine's persistent background loop (sync tool path)."""
        return self._engine.run_sync(coro)

    async def _grant_as_self(
        self, resources: list[str], access: AccessContext
    ) -> AccessContext:
        """Client-credentials grant for `resources` onto `access`, via the engine."""
        return await self._engine.grant_as_self(resources, access)

    def _resources_for(self, request: ToolCallRequest) -> list[str]:
        return self._engine.resources_for(request.tool_call.get("name", ""))

    def _resolve_identity(self, request: ToolCallRequest) -> KeycardIdentity | None:
        """The effective identity for this tool call: context first, then fallback.

        Resolved per call: a sign-in that happens mid-run (via the
        sign_in_required interrupt) is picked up on resume.

        Under identity_source="auth_user" the runtime context is ignored
        entirely and the verified caller on the run config is the only source,
        so a caller cannot name an identity in the request body.
        """
        if self._identity_source == "auth_user":
            caller = caller_from_config(getattr(request.runtime, "config", None))
            if caller is None:
                return None
            return KeycardIdentity(subject_token=caller.subject_token)
        identity = getattr(request.runtime, "context", None)
        if identity is not None and (
            getattr(identity, "subject_token", None)
            or getattr(identity, "user_identifier", None)
            or getattr(identity, "as_self", False)
        ):
            return KeycardIdentity(
                subject_token=getattr(identity, "subject_token", None),
                user_identifier=getattr(identity, "user_identifier", None),
                as_self=getattr(identity, "as_self", False),
            )
        return self._engine.resolve_fallback()

    async def _build_access(self, request: ToolCallRequest) -> AccessContext:
        return await self._engine.build_access(
            self._resolve_identity(request), self._resources_for(request)
        )

    _MAX_AUTHORIZATION_ATTEMPTS = 3

    def _pending_interrupt(
        self, access: AccessContext, request: ToolCallRequest
    ) -> dict[str, Any] | None:
        """The interrupt this AccessContext calls for, if any (as-itself never does)."""
        return self._engine.pending_pause(self._resolve_identity(request), access)

    def _auth_fallback_message(
        self, access: AccessContext, request: ToolCallRequest
    ) -> ToolMessage | None:
        """Failed tool output standing in for the interrupt, or None if none is due.

        Written for a model that must hand the URL to the user: the URL sits on
        its own line, and the instruction is to reproduce it verbatim, because
        a paraphrased or shortened authorization URL does not authorize
        anything.
        """
        payload = self._pending_interrupt(access, request)
        if payload is None:
            return None
        fields = self._engine.fallback_fields(
            payload, str(request.tool_call.get("name", ""))
        )
        content = self._engine.fallback_text(fields)
        return ToolMessage(
            content=content,
            name=fields["tool"] or None,
            tool_call_id=str(request.tool_call.get("id") or ""),
            status="error",
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        access = await self._build_access(request)
        if self._interrupt_on_auth:
            for _ in range(self._MAX_AUTHORIZATION_ATTEMPTS):
                payload = self._pending_interrupt(access, request)
                if payload is None:
                    break
                interrupt(payload)
                access = await self._build_access(request)
        else:
            fallback = self._auth_fallback_message(access, request)
            if fallback is not None:
                return fallback
        token = _current_access.set(access)
        try:
            return await handler(request)
        finally:
            _current_access.reset(token)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        access = self._run_sync(self._build_access(request))
        if self._interrupt_on_auth:
            for _ in range(self._MAX_AUTHORIZATION_ATTEMPTS):
                payload = self._pending_interrupt(access, request)
                if payload is None:
                    break
                interrupt(payload)
                access = self._run_sync(self._build_access(request))
        else:
            fallback = self._auth_fallback_message(access, request)
            if fallback is not None:
                return fallback
        token = _current_access.set(access)
        try:
            return handler(request)
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
        """Serve get_access_context() for code that runs outside an agent.

        Lets the same governed tools back non-agent surfaces, e.g. seeding a
        dashboard panel on page load with the tool the agent uses in chat:

            with keycard.grant(Access.on_behalf_of(token)):
                rows = list_requests.invoke({})

        Also serves resources that have no tool at all, e.g. fetching a
        vaulted LLM key under the agent's own identity:

            with keycard.grant(
                Access.as_self(), resources=[LLM_KEY]
            ) as access:
                key = access.access(LLM_KEY).access_token

        When `identity` is omitted, `fallback_identity` is used. `tool_name`
        applies that tool's `tool_resources` override, `resources` grants
        exactly the listed resources (the two are mutually exclusive), and
        with neither the default resources are granted. There is no run to
        pause, so nothing interrupts here: failures stay on the yielded
        AccessContext, exactly as tools see them.
        """
        resolved, targets = self._engine.grant_target(identity, tool_name, resources)
        access = self._run_sync(self._engine.build_access(resolved, targets))
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
