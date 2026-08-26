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
import base64
import json
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from weakref import WeakKeyDictionary

from langchain_core.messages import ToolMessage
from langgraph.types import Command, interrupt

from keycardai.oauth import AsyncClient, ClientConfig, NoneAuth
from keycardai.oauth.server.access_context import AccessContext
from keycardai.oauth.server.credentials import ApplicationCredential, ClientSecret
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


def _subject_token_expired(token: str) -> bool:
    """Whether a JWT subject token is already expired.

    Decode-only, no signature verification: the zone remains the authority on
    validity. This check exists to route an expiry to sign-in instead of a
    consent page, and to skip an exchange round trip that is guaranteed to
    fail. Opaque or malformed tokens return False and are left for the zone
    to judge.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return False
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    exp = payload.get("exp")
    return isinstance(exp, (int, float)) and exp <= time.time()


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
            resume the exchange is retried. Requires a checkpointer.
        sign_in_url: When set, a run that carries no identity, or whose
            subject token has already expired, pauses with a
            `sign_in_required` interrupt linking here, instead of failing.
            The payload's `reason` field says which case it was. The whole
            flow then lives in the chat: sign in, resume.
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
        application_credential: ApplicationCredential | None = None,
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
        if application_credential is not None and (client_id or client_secret):
            raise ValueError(
                "Pass application_credential or client_id/client_secret, not both"
            )
        self._zone_url = zone_url
        self._resources = list(resources)
        if application_credential is not None:
            self._credential: ApplicationCredential | None = application_credential
        elif client_id and client_secret:
            self._credential = ClientSecret((client_id, client_secret))
        else:
            self._credential = None
        self._tool_resources = tool_resources or {}
        self._request_scopes = request_scopes
        self._authorization_url = authorization_url
        self._sign_in_url = sign_in_url
        self._fallback_identity = fallback_identity
        self._injected_client = client
        self._loop_clients: WeakKeyDictionary[
            asyncio.AbstractEventLoop, AsyncClient
        ] = WeakKeyDictionary()
        self._sync_loop: asyncio.AbstractEventLoop | None = None
        self._sync_loop_lock = threading.Lock()

    def _resolve_fallback(self) -> KeycardIdentity | None:
        fallback = self._fallback_identity
        return fallback() if callable(fallback) else fallback

    def _new_client(self) -> AsyncClient:
        auth = (
            self._credential.get_http_client_auth()
            if self._credential is not None
            else NoneAuth()
        )
        config = ClientConfig(
            enable_metadata_discovery=True,
            auto_register_client=False,
        )
        if self._credential is not None:
            config = self._credential.set_client_config(config, {})
        return AsyncClient(issuer=self._zone_url, auth=auth, config=config)

    def _client(self) -> AsyncClient:
        """The client bound to the running event loop.

        Cached per loop rather than per call: an AsyncClient holds connections
        owned by its loop and must not outlive it. Under an async server the
        loop persists, so this reuses one client (and one metadata discovery)
        for the process. The sync tool path runs on the middleware's own
        persistent loop (see _run_sync), so it shares one warm client too.
        """
        if self._injected_client is not None:
            return self._injected_client
        loop = asyncio.get_running_loop()
        client = self._loop_clients.get(loop)
        if client is None:
            client = self._new_client()
            self._loop_clients[loop] = client
        return client

    def _run_sync(self, coro: Coroutine[Any, Any, AccessContext]) -> AccessContext:
        """Run grant work from the sync path on one persistent background loop.

        asyncio.run would build a fresh event loop per tool call, so the
        per-loop client cache would miss every time and each call would pay
        client construction plus metadata discovery again. One long-lived
        loop keeps a single warm client (and its connections) for every sync
        tool call in the process.
        """
        with self._sync_loop_lock:
            loop = self._sync_loop
            if loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(
                    target=loop.run_forever,
                    name="keycard-grant-middleware",
                    daemon=True,
                ).start()
                self._sync_loop = loop
        return asyncio.run_coroutine_threadsafe(coro, loop).result()

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

    async def _client_auth_fields(
        self, client: AsyncClient, resource: str
    ) -> dict[str, str]:
        """Client-authentication fields the credential puts in the request body.

        Assertion-based credentials (WorkloadIdentity, WebIdentity) carry no
        HTTP-level auth; their proof rides in the request as a jwt-bearer
        client assertion. The protocol only exposes request preparation for
        token exchange, so this prepares one and lifts the auth fields for
        the client-credentials call. ClientSecret authenticates at the HTTP
        layer and contributes nothing here.

        The subject token below is a placeholder: client credentials has no
        subject, the request model requires a non-empty one, and only the
        client-auth fields of the prepared request are read.
        """
        if self._credential is None:
            return {}
        prepared = await self._credential.prepare_token_exchange_request(
            client=client, subject_token="client-credentials", resource=resource
        )
        fields: dict[str, str] = {}
        if getattr(prepared, "client_assertion", None):
            fields["client_assertion"] = prepared.client_assertion
            fields["client_assertion_type"] = prepared.client_assertion_type
        return fields

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
                kwargs.update(await self._client_auth_fields(client, resource))
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
        return await self._build_access_for(
            self._resolve_identity(request), self._resources_for(request)
        )

    async def _build_access_for(
        self, identity: KeycardIdentity | None, resources: list[str]
    ) -> AccessContext:
        access = AccessContext()

        if identity is None:
            access.set_error(
                {
                    "message": (
                        "No Keycard identity for this run. Sign in to continue."
                        if self._sign_in_url
                        else "No Keycard identity on the runtime context. Invoke the "
                        "agent with context=Access.on_behalf_of(...), "
                        "Access.impersonate(...), or "
                        "Access.as_self()."
                    ),
                    "code": "missing_identity",
                }
            )
            return access

        if identity.subject_token and _subject_token_expired(identity.subject_token):
            access.set_error(
                {
                    "message": (
                        "The subject token for this run has expired. "
                        "Sign in again to continue."
                    ),
                    "code": "subject_token_expired",
                }
            )
            return access

        if identity.as_self:
            return await self._grant_as_self(resources, access)

        return await exchange_tokens_for_resources(
            client=self._client(),
            resources=resources,
            subject_token=identity.subject_token or "",
            access_context=access,
            application_credential=self._credential,
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

    def _sign_in_payload(self, access: AccessContext) -> dict[str, Any]:
        error = access.get_error() or {}
        reason = error.get("code", "missing_identity")
        return {
            "type": "sign_in_required",
            "sign_in_url": self._sign_in_url,
            "reason": reason,
            "message": (
                "Your session has expired. Sign in again, then resume the run."
                if reason == "subject_token_expired"
                else "Sign in with Keycard to continue. Open the link, sign in, "
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
            return self._sign_in_payload(access)
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
        access = self._run_sync(self._build_access(request))
        for _ in range(self._MAX_AUTHORIZATION_ATTEMPTS):
            payload = self._pending_interrupt(access, request)
            if payload is None:
                break
            interrupt(payload)
            access = self._run_sync(self._build_access(request))
        token = _current_access.set(access)
        try:
            return handler(request)
        finally:
            _current_access.reset(token)

    def _grant_target(
        self,
        identity: KeycardIdentity | None,
        tool_name: str | None,
        resources: list[str] | None,
    ) -> tuple[KeycardIdentity | None, list[str]]:
        if tool_name is not None and resources is not None:
            raise ValueError("Pass tool_name or resources, not both")
        resolved = identity if identity is not None else self._resolve_fallback()
        if resources is not None:
            return resolved, list(resources)
        if tool_name is not None:
            return resolved, self._tool_resources.get(tool_name, self._resources)
        return resolved, self._resources

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
        resolved, targets = self._grant_target(identity, tool_name, resources)
        access = self._run_sync(self._build_access_for(resolved, targets))
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
        resolved, targets = self._grant_target(identity, tool_name, resources)
        access = await self._build_access_for(resolved, targets)
        token = _current_access.set(access)
        try:
            yield access
        finally:
            _current_access.reset(token)
