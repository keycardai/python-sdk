"""Actor resolution and acquisition shared by the middleware, the CLI and the relay."""

from __future__ import annotations

import shlex
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from keycardai.oauth import AsyncClient
from keycardai.oauth.exceptions import OAuthProtocolError
from keycardai.oauth.server import (
    Access,
    AccessContext,
    AgentGrantEngine,
    KeycardIdentity,
)

from .settings import HermesSettings
from .store import EXPIRY_SKEW_SECONDS, SignIn, SignInStore

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

    def run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        return self.engine.run_sync(coro)

    def fresh_sign_in(self, actor: str) -> SignIn:
        """The stored sign-in, refreshed when within the skew of its expiry.

        Raises ``SignInRequired`` for no sign-in, no refresh token or a refresh
        rejected with ``invalid_grant``; ``RefreshFailed`` for any other
        refresh failure. At most one refresh per sign-in runs at a time across
        threads and processes; a waiter re-reads the store and uses the
        other's result.
        """
        sign_in = self.store.load(actor)
        if sign_in is None:
            raise SignInRequired("missing_identity", actor)
        if not sign_in.near_expiry(EXPIRY_SKEW_SECONDS, self.clock()):
            return sign_in
        if not sign_in.refresh_token:
            raise SignInRequired("subject_token_expired", actor)
        with self.store.lock(actor):
            current = self.store.load(actor)
            if current is None:
                raise SignInRequired("missing_identity", actor)
            if not current.near_expiry(EXPIRY_SKEW_SECONDS, self.clock()):
                return current
            if not current.refresh_token:
                raise SignInRequired("subject_token_expired", actor)
            return self._refresh(actor, current)

    def _public(self) -> AsyncClient:
        """The unauthenticated client the public sign-in client's requests go
        through: the engine's client carries the application's own client
        authentication, which must not be sent with a public client_id.
        Built on the running loop (the engine's persistent loop)."""
        if self.public_client is None:
            self.public_client = AsyncClient(issuer=self.settings.zone_url)
        return self.public_client

    async def _refresh_coro(self, sign_in: SignIn) -> Any:
        client = self._public()
        resources = [self.settings.sign_in_resource] if self.settings.sign_in_resource else None
        return await client.refresh_token_grant(
            refresh_token=sign_in.refresh_token or "",
            client_id=sign_in.client_id,
            resources=resources,
        )

    def _refresh(self, actor: str, sign_in: SignIn) -> SignIn:
        try:
            response = self.run(self._refresh_coro(sign_in))
        except OAuthProtocolError as exc:
            if exc.error == "invalid_grant":
                raise SignInRequired("subject_token_expired", actor) from exc
            raise RefreshFailed(str(exc)) from exc
        except Exception as exc:
            raise RefreshFailed(str(exc)) from exc
        expires_in = response.expires_in if response.expires_in is not None else 300
        refreshed = SignIn(
            client_id=sign_in.client_id,
            refresh_token=response.refresh_token or sign_in.refresh_token,
            access_token=response.access_token,
            expires_at=self.clock() + float(expires_in),
        )
        self.store.save(actor, refreshed)
        return refreshed

    def identity_for(self, actor: str | None, pattern: str | None = None) -> KeycardIdentity:
        """The identity for ``actor`` under ``pattern`` (default
        ``default_pattern``); raises ``SignInRequired`` or ``RefreshFailed``."""
        pattern = pattern or self.settings.default_pattern
        if pattern == "as_self":
            return Access.as_self()
        if actor is None:
            raise SignInRequired("missing_identity", None)
        if pattern == "impersonate":
            user = self.settings.actors.get(actor)
            if not user:
                raise SignInRequired("missing_identity", actor)
            return Access.impersonate(user)
        return Access.on_behalf_of(self.fresh_sign_in(actor).access_token)

    def resolve(
        self, actor: str | None, resources: list[str], pattern: str | None = None
    ) -> tuple[KeycardIdentity | None, AccessContext | None, SignInRequired | None]:
        """The identity for ``actor``, or the AccessContext that records why
        there is none. Never raises: a missing or expired sign-in is a
        context error, a failed refresh a per-resource error."""
        pattern = pattern or self.settings.default_pattern
        try:
            return self.identity_for(actor, pattern), None, None
        except SignInRequired as needed:
            access = AccessContext()
            access.set_error(
                {
                    "message": sign_in_message(needed.reason, needed.actor),
                    "code": needed.reason,
                }
            )
            return None, access, needed
        except RefreshFailed as exc:
            access = AccessContext()
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
        """``acquire`` on the running loop, for ``agrant``."""
        identity, access, _ = self.resolve(actor, resources, pattern)
        if access is not None:
            return access
        return await self.engine.build_access(identity, resources)


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
