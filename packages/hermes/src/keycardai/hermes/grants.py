"""Actor resolution and acquisition shared by the middleware, the CLI and the relay."""

from __future__ import annotations

import asyncio
import shlex
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, cast

from keycardai.oauth import AsyncClient
from keycardai.oauth.exceptions import OAuthProtocolError
from keycardai.oauth.server import (
    Access,
    AccessContext,
    AgentGrantEngine,
    KeycardIdentity,
)

from .settings import HermesSettings
from .store import EXPIRY_SKEW_SECONDS, LockHeldByThisLoop, SignIn, SignInStore

LOGIN_COMMAND = "hermes keycard login --actor {actor}"


class SignInRequired(Exception):
    """The actor has no usable sign-in: ``reason`` is ``missing_identity`` or
    ``subject_token_expired``; ``actor`` is the resolved actor when known."""

    def __init__(self, reason: str, actor: str | None) -> None:
        self.reason = reason
        self.actor = actor
        super().__init__(reason)


class RefreshFailed(Exception):
    """A refresh failed for a reason other than ``invalid_grant``; the stored
    sign-in is unchanged and this call records a per-resource error."""


def resolve_actor(settings: HermesSettings, bound: str | None, *, chat_turn: bool = False) -> str | None:
    """The actor a turn acts as, through ``actor_aliases``: the bound sender;
    else ``owner_actor`` for a turn with no sender on a non-messaging surface
    (api_server, CLI, TUI, cron, kanban). A turn on a messaging surface with
    an empty actor (``chat_turn``) has no single sender (Hermes clears it when
    another sender's message joins a running turn,
    gateway/session_context.py:224-225), so it gets no actor at all rather
    than the owner's."""
    if bound:
        actor: str | None = bound
    elif chat_turn:
        return None
    else:
        actor = settings.owner_actor
    if actor is None:
        return None
    return settings.actor_aliases.get(actor, actor)


@dataclass
class Grants:
    """One profile's acquisition path, over the engine's persistent loop."""

    settings: HermesSettings
    engine: AgentGrantEngine
    store: SignInStore
    clock: Callable[[], float] = time.time
    public_client: AsyncClient | None = None
    _engine_public: dict[str, AsyncClient] = field(default_factory=dict, init=False, repr=False)

    def run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        return self.engine.run_sync(coro)

    def _checked(self, actor: str, sign_in: SignIn | None) -> tuple[SignIn, bool]:
        """``sign_in`` and whether it needs a refresh first. Raises
        ``SignInRequired`` for no sign-in, or for one near expiry with no
        refresh token."""
        if sign_in is None:
            raise SignInRequired("missing_identity", actor)
        if not sign_in.near_expiry(EXPIRY_SKEW_SECONDS, self.clock()):
            return sign_in, False
        if not sign_in.refresh_token:
            raise SignInRequired("subject_token_expired", actor)
        return sign_in, True

    def fresh_sign_in(self, actor: str) -> SignIn:
        """The stored sign-in, refreshed when within the skew of its expiry.

        Raises ``SignInRequired`` for no sign-in, no refresh token or a refresh
        rejected with ``invalid_grant``; ``RefreshFailed`` for any other
        refresh failure. At most one refresh per sign-in runs at a time across
        threads and processes; a waiter re-reads the store and uses the
        other's result.

        Called on a thread whose running loop has an ``agrant`` refresh of
        the same sign-in in flight, waiting for its lock would hang that
        loop, so the call raises ``RefreshFailed`` (a retryable per-resource
        error) instead of waiting.
        """
        sign_in, stale = self._checked(actor, self.store.load(actor))
        if not stale:
            return sign_in
        try:
            with self.store.lock(actor):
                current, stale = self._checked(actor, self.store.load(actor))
                if not stale:
                    return current
                try:
                    response = self.run(self._refresh_coro(current, on_engine=True))
                except Exception as exc:
                    raise _refresh_error(actor, exc) from exc
                return self._store_refreshed(actor, current, response)
        except LockHeldByThisLoop as exc:
            raise RefreshFailed(str(exc)) from exc

    async def afresh_sign_in(self, actor: str) -> SignIn:
        """``fresh_sign_in`` on the running loop: the sign-in lock is taken
        with ``SignInStore.alock`` and the refresh is awaited here, so other
        tasks on the loop keep running while it is in flight. The lock is the
        same one ``fresh_sign_in`` takes, so sync and async callers still
        share one refresh per sign-in.

        The refresh and the save run as their own task, shielded from the
        caller: the zone may rotate the refresh token as soon as it gets the
        request, so a caller cancelled mid refresh still waits, holding the
        lock, until the replacement is saved, and then raises
        ``CancelledError``."""
        sign_in, stale = self._checked(actor, self.store.load(actor))
        if not stale:
            return sign_in
        async with self.store.alock(actor):
            current, stale = self._checked(actor, self.store.load(actor))
            if not stale:
                return current
            return await _uncancellable(self._arefresh_and_store(actor, current))

    async def _arefresh_and_store(self, actor: str, sign_in: SignIn) -> SignIn:
        try:
            response = await self._refresh_coro(sign_in)
        except Exception as exc:
            raise _refresh_error(actor, exc) from exc
        return self._store_refreshed(actor, sign_in, response)

    async def _refresh_coro(self, sign_in: SignIn, *, on_engine: bool = False) -> Any:
        owned = False
        if self.public_client is not None:
            client = self.public_client
        elif on_engine:
            # Reuse public clients on the persistent engine loop, but never
            # send its application authentication or another sign-in's ID.
            client = self._engine_public.get(sign_in.client_id)
            if client is None:
                client = AsyncClient(issuer=self.settings.zone_url, client_id=sign_in.client_id)
                self._engine_public[sign_in.client_id] = client
        else:
            # One client per refresh on the caller's loop: a refresh runs once
            # per access token lifetime, and a client kept past this loop
            # would hold the loop through its transport after it closes.
            client = AsyncClient(issuer=self.settings.zone_url, client_id=sign_in.client_id)
            owned = True
        try:
            resources = [self.settings.sign_in_resource] if self.settings.sign_in_resource else None
            return await client.refresh_token_grant(
                refresh_token=sign_in.refresh_token or "",
                resources=resources,
            )
        finally:
            if owned:
                await client.aclose()

    def _store_refreshed(self, actor: str, sign_in: SignIn, response: Any) -> SignIn:
        expires_in = response.expires_in if response.expires_in is not None else 300
        refreshed = SignIn(
            client_id=sign_in.client_id,
            refresh_token=response.refresh_token or sign_in.refresh_token,
            access_token=response.access_token,
            expires_at=self.clock() + float(expires_in),
        )
        self.store.save(actor, refreshed)
        return refreshed

    def _identity_without_sign_in(self, actor: str | None, pattern: str) -> KeycardIdentity | None:
        """The identity for a ``pattern`` that needs no stored sign-in, or
        None for ``on_behalf_of`` with an actor. Raises ``SignInRequired``
        when there is no actor or no ``actors`` entry to impersonate."""
        if pattern == "as_self":
            return Access.as_self()
        if actor is None:
            raise SignInRequired("missing_identity", None)
        if pattern == "impersonate":
            user = self.settings.actors.get(actor)
            if not user:
                raise SignInRequired("missing_identity", actor)
            return Access.impersonate(user)
        return None

    def identity_for(self, actor: str | None, pattern: str | None = None) -> KeycardIdentity:
        """The identity for ``actor`` under ``pattern`` (default
        ``default_pattern``); raises ``SignInRequired`` or ``RefreshFailed``."""
        pattern = pattern or self.settings.default_pattern
        identity = self._identity_without_sign_in(actor, pattern)
        if identity is not None:
            return identity
        return Access.on_behalf_of(self.fresh_sign_in(cast(str, actor)).access_token)

    async def aidentity_for(self, actor: str | None, pattern: str | None = None) -> KeycardIdentity:
        """``identity_for`` with the refresh awaited on the running loop."""
        pattern = pattern or self.settings.default_pattern
        identity = self._identity_without_sign_in(actor, pattern)
        if identity is not None:
            return identity
        sign_in = await self.afresh_sign_in(cast(str, actor))
        return Access.on_behalf_of(sign_in.access_token)

    def resolve(
        self, actor: str | None, resources: list[str], pattern: str | None = None
    ) -> tuple[KeycardIdentity | None, AccessContext | None, SignInRequired | None]:
        """The identity for ``actor``, or the AccessContext that records why
        there is none. Never raises: a missing or expired sign-in is a
        context error, a failed refresh a per-resource error."""
        try:
            return self.identity_for(actor, pattern), None, None
        except (SignInRequired, RefreshFailed) as exc:
            return _unresolved(exc, resources)

    async def aresolve(
        self, actor: str | None, resources: list[str], pattern: str | None = None
    ) -> tuple[KeycardIdentity | None, AccessContext | None, SignInRequired | None]:
        """``resolve`` with the refresh awaited on the running loop."""
        try:
            return await self.aidentity_for(actor, pattern), None, None
        except (SignInRequired, RefreshFailed) as exc:
            return _unresolved(exc, resources)

    def acquire(
        self, actor: str | None, resources: list[str], pattern: str | None = None
    ) -> tuple[AccessContext, SignInRequired | None]:
        """Acquire ``resources`` for ``actor`` on the engine's loop: the
        AccessContext, and the sign-in the call needs when it has none."""
        identity, access, needed = self.resolve(actor, resources, pattern)
        if access is not None:
            return access, needed
        return self.run(self.engine.build_access(identity, resources)), None

    async def aacquire(
        self, actor: str | None, resources: list[str], pattern: str | None = None
    ) -> AccessContext:
        """``acquire`` on the running loop, for ``agrant``: the refresh and
        the sign-in lock are awaited, so the loop is never blocked."""
        identity, access, _ = await self.aresolve(actor, resources, pattern)
        if access is not None:
            return access
        return await self.engine.build_access(identity, resources)


async def _uncancellable(coro: Coroutine[Any, Any, SignIn]) -> SignIn:
    """Await ``coro`` as a task the caller's cancellation cannot interrupt.
    A cancelled caller waits for the task to finish, then raises
    ``CancelledError``, so the cancellation still reaches it (and an
    ``asyncio.timeout`` around it still turns into ``TimeoutError``)."""
    task = asyncio.ensure_future(coro)
    cancelled = False
    while not task.done():
        try:
            # wait() returns when the task finishes, whether it succeeded or
            # failed, and a cancelled waiter leaves the task running, so the
            # only exception here is the caller's own cancellation.
            await asyncio.wait({task})
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        if not task.cancelled():
            # Retrieved so a failed refresh is not logged as never retrieved.
            task.exception()
        raise asyncio.CancelledError
    return task.result()


def _refresh_error(actor: str, exc: Exception) -> SignInRequired | RefreshFailed:
    """A refresh failure routed by the spec: ``invalid_grant`` needs a new
    sign-in, anything else is ``RefreshFailed``."""
    if isinstance(exc, OAuthProtocolError) and exc.error == "invalid_grant":
        return SignInRequired("subject_token_expired", actor)
    return RefreshFailed(str(exc))


def _unresolved(
    exc: SignInRequired | RefreshFailed, resources: list[str]
) -> tuple[None, AccessContext, SignInRequired | None]:
    """The AccessContext recording why there is no identity: a context error
    for a missing or expired sign-in, a per-resource error for a failed
    refresh."""
    access = AccessContext()
    if isinstance(exc, SignInRequired):
        access.set_error(
            {
                "message": sign_in_message(exc.reason, exc.actor),
                "code": exc.reason,
            }
        )
        return None, access, exc
    for resource in resources:
        access.set_resource_error(
            resource,
            {
                "message": f"The stored sign-in could not be refreshed: {exc}",
                "code": "refresh_failed",
                "retryable": True,
            },
        )
    return None, access, None


def login_command(actor: str | None) -> str:
    """The login command for ``actor``, shell-quoted: the actor is a sender id
    from a chat platform and the text asks the operator to paste it."""
    return LOGIN_COMMAND.format(actor=shlex.quote(actor)) if actor else "hermes keycard login"


def sign_in_message(reason: str, actor: str | None) -> str:
    command = login_command(actor)
    if reason == "subject_token_expired":
        return f"The sign-in for this actor has expired. Run `{command}` on the Hermes host."
    return f"No Keycard sign-in for this actor. Run `{command}` on the Hermes host."


def sign_in_text(reason: str, actor: str | None, tool_name: str) -> str:
    """The blocking tool result for a call that needs a sign-in: the LangChain
    fallback shape, with the login command where a URL would be, since the
    sign-in runs on the host and no URL can complete it."""
    command = login_command(actor)
    return (
        f"sign_in_required: the tool {tool_name} cannot run yet (reason: {reason}).\n"
        f"{command}\n"
        "Tell the user to run the command above in a shell on the Hermes host to "
        "sign in. Copy it into your reply exactly as written. Then ask the user to "
        f"tell you once they are done, and call {tool_name} again."
    )
