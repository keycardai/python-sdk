"""Keycard grant middleware for LangChain 1.x agents.

Grants delegated access at the tool-call boundary: before each tool executes,
the middleware exchanges the caller's identity for short-lived resource tokens
(RFC 8693) via the shared keycardai-oauth orchestration, and exposes the result
to the tool as a non-throwing AccessContext.

The same middleware instance works under `create_agent` and `create_deep_agent`
(deepagents is built on the create_agent middleware system).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from weakref import WeakKeyDictionary

from langchain_core.messages import ToolMessage
from langgraph.types import Command, interrupt

from keycardai.oauth import AsyncClient, BasicAuth, ClientConfig, NoneAuth
from keycardai.oauth.server.access_context import AccessContext
from keycardai.oauth.server.token_exchange import exchange_tokens_for_resources
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest


@dataclass
class KeycardIdentity:
    """Per-invocation identity, passed as the agent's runtime context.

    Exactly one of the three should be set:
    - subject_token: on-behalf-of. The caller's Keycard access token, exchanged
      per tool call for resource tokens (RFC 8693).
    - user_identifier: impersonation. A substitute-user exchange for this user,
      authenticated by the agent's own application credential. Forbidden by
      default; requires an explicit policy in the zone.
    - as_self=True: the agent acts as itself (client credentials). No user
      anywhere: resource access is attributed to the application alone. This is
      deliberately explicit; a run with no identity at all stays an error (or a
      sign-in interrupt), never silently escalates to the agent's own authority.
    """

    subject_token: str | None = None
    user_identifier: str | None = None
    as_self: bool = False

    def __bool__(self) -> bool:
        return bool(self.subject_token or self.user_identifier or self.as_self)


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
            "to the agent's middleware list and invoke the agent with a "
            "KeycardIdentity context."
        )
    return access


class KeycardGrantMiddleware(AgentMiddleware):
    """Exchange the caller's identity for resource tokens on every tool call.

    Args:
        zone_url: Keycard zone URL (issuer). Required unless `client` is given.
        resources: Resource URLs to grant for every tool call.
        client_id / client_secret: The agent's application credential. Used to
            authenticate the exchange; required for impersonation.
        tool_resources: Optional per-tool override, tool name -> resource URLs.
            Tools absent from the map get `resources`.
        request_scopes: Optional outbound scopes for the exchange, same shapes
            as the core orchestrator (str | list | dict per resource).
        authorization_url: When set, a failed exchange pauses the run with a
            LangGraph interrupt instead of recording a silent error. The
            interrupt payload carries this URL (str, or callable taking the
            failed resource URLs) for the user to establish the grant; on
            resume the exchange is retried. Requires a checkpointer.
        sign_in_url: When set, a run that carries no identity at all pauses
            with a `sign_in_required` interrupt linking here, instead of
            failing. The whole flow then lives in the chat: sign in, resume.
        fallback_identity: Identity used when the runtime context carries
            none. Pass a callable to resolve it per tool call, so a sign-in
            that happens mid-run is picked up on resume without a restart.
        client: Injectable AsyncClient (tests). When set, zone_url is unused
            and the client is reused as-is.
    """

    def __init__(
        self,
        *,
        zone_url: str | None = None,
        resources: list[str],
        client_id: str | None = None,
        client_secret: str | None = None,
        tool_resources: dict[str, list[str]] | None = None,
        request_scopes: str | list[str] | dict[str, str | list[str]] | None = None,
        authorization_url: str | Callable[[list[str]], str] | None = None,
        sign_in_url: str | None = None,
        fallback_identity: KeycardIdentity
        | Callable[[], KeycardIdentity | None]
        | None = None,
        client: AsyncClient | None = None,
    ) -> None:
        super().__init__()
        if client is None and not zone_url:
            raise ValueError(
                "KeycardGrantMiddleware requires zone_url (or an injected client)"
            )
        self._zone_url = zone_url
        self._resources = list(resources)
        self._client_id = client_id
        self._client_secret = client_secret
        self._tool_resources = tool_resources or {}
        self._request_scopes = request_scopes
        self._authorization_url = authorization_url
        self._sign_in_url = sign_in_url
        self._fallback_identity = fallback_identity
        self._injected_client = client
        self._loop_clients: WeakKeyDictionary[
            asyncio.AbstractEventLoop, AsyncClient
        ] = WeakKeyDictionary()

    def _resolve_fallback(self) -> KeycardIdentity | None:
        fallback = self._fallback_identity
        return fallback() if callable(fallback) else fallback

    def _new_client(self) -> AsyncClient:
        auth = (
            BasicAuth(self._client_id, self._client_secret)
            if self._client_id and self._client_secret
            else NoneAuth()
        )
        return AsyncClient(
            issuer=self._zone_url,
            auth=auth,
            config=ClientConfig(
                enable_metadata_discovery=True,
                auto_register_client=False,
            ),
        )

    def _client(self) -> AsyncClient:
        """The client bound to the running event loop.

        Cached per loop rather than per call: an AsyncClient holds connections
        owned by its loop and must not outlive it. Under an async server the
        loop persists, so this reuses one client (and one metadata discovery)
        for the process. Sync callers that reach here through `asyncio.run`
        get a fresh loop each time and therefore a fresh client.
        """
        if self._injected_client is not None:
            return self._injected_client
        loop = asyncio.get_running_loop()
        client = self._loop_clients.get(loop)
        if client is None:
            client = self._new_client()
            self._loop_clients[loop] = client
        return client

    def _resources_for(self, request: ToolCallRequest) -> list[str]:
        name = request.tool_call.get("name", "")
        return self._tool_resources.get(name, self._resources)

    def _resolve_identity(self, request: ToolCallRequest) -> KeycardIdentity | None:
        """The effective identity for this tool call: context first, then fallback.

        Resolved per call: a sign-in that happens mid-run (via the
        sign_in_required interrupt) is picked up on resume.
        """
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
        return self._resolve_fallback()

    def _scope_for(self, resource: str) -> str | None:
        scopes = self._request_scopes
        if scopes is None:
            return None
        value = scopes.get(resource) if isinstance(scopes, dict) else scopes
        if value is None:
            return None
        return " ".join(value) if isinstance(value, list) else value

    async def _grant_as_self(
        self, resources: list[str], access: AccessContext
    ) -> AccessContext:
        """Client-credentials acquisition: the agent's own authority, no subject.

        Not routed through exchange_tokens_for_resources(), which only models
        subject-token flows.
        """
        client = self._client()
        for resource in resources:
            try:
                kwargs: dict[str, Any] = {"resource": resource}
                scope = self._scope_for(resource)
                if scope:
                    kwargs["scope"] = scope
                token = await client.client_credentials_grant(**kwargs)
                access.set_token(resource, token)
            except Exception as e:
                error: dict[str, str] = {
                    "message": f"Client credentials grant failed for {resource}"
                }
                if hasattr(e, "error"):
                    error["code"] = e.error
                if getattr(e, "error_description", None):
                    error["description"] = e.error_description
                if "code" not in error:
                    error["raw_error"] = str(e)
                access.set_resource_error(resource, error)
        return access

    async def _build_access(self, request: ToolCallRequest) -> AccessContext:
        access = AccessContext()
        identity = self._resolve_identity(request)

        if identity is None:
            access.set_error(
                {
                    "message": (
                        "No Keycard identity for this run. Sign in to continue."
                        if self._sign_in_url
                        else "No Keycard identity on the runtime context. Invoke the "
                        "agent with context=KeycardIdentity(subject_token=...), "
                        "KeycardIdentity(user_identifier=...), or "
                        "KeycardIdentity(as_self=True)."
                    ),
                    "code": "missing_identity",
                }
            )
            return access

        if identity.as_self:
            return await self._grant_as_self(self._resources_for(request), access)

        return await exchange_tokens_for_resources(
            client=self._client(),
            resources=self._resources_for(request),
            subject_token=identity.subject_token or "",
            access_context=access,
            user_identifier=identity.user_identifier,
            request_scopes=self._request_scopes,
        )

    _MAX_AUTHORIZATION_ATTEMPTS = 3

    def _interrupt_payload(
        self, failed: list[str], access: AccessContext
    ) -> dict[str, Any]:
        url = self._authorization_url
        if callable(url):
            url = url(failed)
        return {
            "type": "authorization_required",
            "resources": failed,
            "authorization_url": url,
            "errors": {r: access.get_resource_error(r) for r in failed},
            "message": (
                "Access to the resources above has not been granted yet. "
                "Open the authorization URL to grant it, then resume the run."
            ),
        }

    def _sign_in_payload(self) -> dict[str, Any]:
        return {
            "type": "sign_in_required",
            "sign_in_url": self._sign_in_url,
            "message": (
                "Sign in with Keycard to continue. Open the link, sign in, "
                "then resume the run."
            ),
        }

    def _pending_interrupt(
        self, access: AccessContext, request: ToolCallRequest
    ) -> dict[str, Any] | None:
        """The interrupt this AccessContext calls for, if any.

        As-itself runs never interrupt: there is no user to send to a sign-in
        or consent page, so failures stay on the AccessContext as errors for
        the tool (and the operator's logs) to surface.
        """
        identity = self._resolve_identity(request)
        if identity is not None and identity.as_self:
            return None
        if access.has_error() and self._sign_in_url:
            return self._sign_in_payload()
        failed = access.get_failed_resources()
        if failed and self._authorization_url is not None:
            return self._interrupt_payload(failed, access)
        return None

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        access = await self._build_access(request)
        for _ in range(self._MAX_AUTHORIZATION_ATTEMPTS):
            payload = self._pending_interrupt(access, request)
            if payload is None:
                break
            interrupt(payload)
            access = await self._build_access(request)
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
        access = asyncio.run(self._build_access(request))
        for _ in range(self._MAX_AUTHORIZATION_ATTEMPTS):
            payload = self._pending_interrupt(access, request)
            if payload is None:
                break
            interrupt(payload)
            access = asyncio.run(self._build_access(request))
        token = _current_access.set(access)
        try:
            return handler(request)
        finally:
            _current_access.reset(token)
