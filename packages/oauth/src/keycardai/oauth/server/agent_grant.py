"""Shared grant engine for agent-side framework integrations.

Framework-free acquisition logic for integrations that grant delegated access
at an agent's tool-call boundary (keycardai-langchain, keycardai-pydantic-ai).
The engine takes configuration plus a resolved KeycardIdentity and returns a
non-throwing AccessContext; it holds no per-run state and imports no agent
framework. Each integration keeps its own context variable, accessor, testing
seam and pause mechanism, and delegates everything else here.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, TypedDict
from weakref import WeakKeyDictionary

from keycardai.oauth import AsyncClient, ClientConfig, NoneAuth
from keycardai.oauth.exceptions import OAuthProtocolError

from .access_context import AccessContext
from .credentials import ApplicationCredential, ClientSecret
from .token_exchange import error_retryable, exchange_tokens_for_resources

ToolResources = dict[str, list[str]] | Callable[[str], list[str] | None]
"""Per-tool resource override: a map of tool name to resources, or a callable
asked for each tool name that returns the resources or None for the default."""


@dataclass
class KeycardIdentity:
    """Per-run identity an agent acts under.

    Exactly one of the three should be set:
    - subject_token: on-behalf-of. The caller's Keycard access token, exchanged
      per tool call for resource tokens (RFC 8693).
    - user_identifier: impersonation. A substitute-user exchange for this user,
      authenticated by the agent's own application credential. Forbidden by
      default; requires an explicit policy in the zone.
    - as_self=True: the agent acts as itself (client credentials). No user
      anywhere: resource access is attributed to the application alone. This is
      deliberately explicit; a run with no identity at all stays an error (or a
      sign-in pause), never silently escalates to the agent's own authority.
    """

    subject_token: str | None = None
    user_identifier: str | None = None
    as_self: bool = False

    def __bool__(self) -> bool:
        return bool(self.subject_token or self.user_identifier or self.as_self)


class Access:
    """Namespace of factories for the identity a run acts under.

    Each classmethod builds the KeycardIdentity for one access pattern, so a
    call site names the pattern instead of setting a field.
    """

    def __init__(self) -> None:
        raise TypeError(
            "Access is a namespace of factories, not a type. Call "
            "Access.as_self(), Access.on_behalf_of(subject_token), or "
            "Access.impersonate(user_identifier)."
        )

    @classmethod
    def as_self(cls) -> KeycardIdentity:
        """The agent acts as its own application: client credentials, no user."""
        return KeycardIdentity(as_self=True)

    @classmethod
    def on_behalf_of(cls, subject_token: str) -> KeycardIdentity:
        """The agent acts for the caller, exchanging the caller's token (RFC 8693)."""
        if not subject_token or not subject_token.strip():
            raise ValueError("Access.on_behalf_of requires a non-empty subject token")
        return KeycardIdentity(subject_token=subject_token)

    @classmethod
    def impersonate(cls, user_identifier: str) -> KeycardIdentity:
        """The agent acts as a named user, authenticated by its own credential."""
        if not user_identifier or not user_identifier.strip():
            raise ValueError("Access.impersonate requires a non-empty user identifier")
        return KeycardIdentity(user_identifier=user_identifier)


def subject_token_expired(token: str) -> bool:
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


class _ClientAuthFields(TypedDict, total=False):
    client_assertion: str
    client_assertion_type: str
    client_id: str


class AgentGrantEngine:
    """Acquire an AccessContext for a resolved identity and a set of resources.

    Args:
        zone_url: Keycard zone URL (issuer). Required unless `client` is given.
        resources: Resource URLs granted by default.
        application_credential: How the agent authenticates to the zone.
            Mutually exclusive with client_id / client_secret.
        client_id / client_secret: Shorthand for
            `application_credential=ClientSecret((client_id, client_secret))`.
        tool_resources: Per-tool override, a map of tool name to resource URLs
            or a callable taking the tool name. A tool absent from the map, or
            for which the callable returns None, gets `resources`.
        request_scopes: Outbound scopes for the exchange and the
            client-credentials grant (str | list | dict per resource).
        authorization_url: Where a user establishes a missing grant (str, or
            callable taking the failed resource URLs). When set,
            `pending_pause` reports an `authorization_required` payload for
            failed resources.
        sign_in_url: Where a user signs in. When set, `pending_pause` reports
            a `sign_in_required` payload for a run with no identity or an
            expired subject token.
        fallback_identity: Identity used when the integration resolves none.
            A callable is resolved per call through `resolve_fallback()`.
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
        tool_resources: ToolResources | None = None,
        request_scopes: str | list[str] | dict[str, str | list[str]] | None = None,
        authorization_url: str | Callable[[list[str]], str] | None = None,
        sign_in_url: str | None = None,
        fallback_identity: KeycardIdentity
        | Callable[[], KeycardIdentity | None]
        | None = None,
        client: AsyncClient | None = None,
    ) -> None:
        if client is None and not zone_url:
            raise ValueError("zone_url is required unless a client is injected")
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
        self._tool_resources: ToolResources = tool_resources or {}
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

    @property
    def resources(self) -> list[str]:
        """The default resources."""
        return list(self._resources)

    @property
    def credential(self) -> ApplicationCredential | None:
        """The configured application credential, if any."""
        return self._credential

    @property
    def sign_in_url(self) -> str | None:
        return self._sign_in_url

    @property
    def authorization_url(self) -> str | Callable[[list[str]], str] | None:
        return self._authorization_url

    # Identity and resource selection

    def resolve_fallback(self) -> KeycardIdentity | None:
        """The configured fallback identity, resolved now if it is a callable."""
        fallback = self._fallback_identity
        if isinstance(fallback, KeycardIdentity) or fallback is None:
            return fallback
        return fallback()

    def resources_for(self, tool_name: str) -> list[str]:
        """The resources to grant for a tool: its override, else the default."""
        override = self._tool_resources
        if isinstance(override, dict):
            return override.get(tool_name, self._resources)
        selected = override(tool_name)
        return self._resources if selected is None else list(selected)

    def grant_target(
        self,
        identity: KeycardIdentity | None,
        tool_name: str | None,
        resources: list[str] | None,
    ) -> tuple[KeycardIdentity | None, list[str]]:
        """Resolve what an escape-hatch grant acquires, and for whom.

        `tool_name` applies that tool's override, `resources` grants exactly
        the listed resources (the two are mutually exclusive), and with
        neither the default resources are granted. An omitted identity falls
        back to `fallback_identity`.
        """
        if tool_name is not None and resources is not None:
            raise ValueError("Pass tool_name or resources, not both")
        resolved = identity if identity is not None else self.resolve_fallback()
        if resources is not None:
            return resolved, list(resources)
        if tool_name is not None:
            return resolved, self.resources_for(tool_name)
        return resolved, self._resources

    # Clients

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

    def client(self) -> AsyncClient:
        """The client bound to the running event loop.

        Cached per loop rather than per call: an AsyncClient holds connections
        owned by its loop and must not outlive it. Under an async server the
        loop persists, so this reuses one client (and one metadata discovery)
        for the process. The sync path runs on the engine's own persistent
        loop (see run_sync), so it shares one warm client too.
        """
        if self._injected_client is not None:
            return self._injected_client
        loop = asyncio.get_running_loop()
        client = self._loop_clients.get(loop)
        if client is None:
            client = self._new_client()
            self._loop_clients[loop] = client
        return client

    def run_sync(self, coro: Coroutine[Any, Any, AccessContext]) -> AccessContext:
        """Run grant work from a sync path on one persistent background loop.

        asyncio.run would build a fresh event loop per call, so the per-loop
        client cache would miss every time and each call would pay client
        construction plus metadata discovery again. One long-lived loop keeps
        a single warm client (and its connections) for every sync call in the
        process.
        """
        with self._sync_loop_lock:
            loop = self._sync_loop
            if loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(
                    target=loop.run_forever,
                    name="keycard-agent-grant",
                    daemon=True,
                ).start()
                self._sync_loop = loop
        return asyncio.run_coroutine_threadsafe(coro, loop).result()

    # Acquisition

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
    ) -> _ClientAuthFields:
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
        fields: _ClientAuthFields = {}
        if self._credential is None:
            return fields
        prepared = await self._credential.prepare_token_exchange_request(
            client=client, subject_token="client-credentials", resource=resource
        )
        if prepared.client_assertion:
            fields["client_assertion"] = prepared.client_assertion
            if prepared.client_assertion_type:
                fields["client_assertion_type"] = prepared.client_assertion_type
            if prepared.client_id:
                fields["client_id"] = prepared.client_id
        return fields

    async def grant_as_self(
        self, resources: list[str], access: AccessContext
    ) -> AccessContext:
        """Client-credentials acquisition: the agent's own authority, no subject.

        Not routed through exchange_tokens_for_resources(), which only models
        subject-token flows.
        """
        client = self.client()
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
                error: dict[str, str | bool] = {
                    "message": f"Client credentials grant failed for {resource}",
                    "retryable": error_retryable(e),
                }
                if isinstance(e, OAuthProtocolError):
                    error["code"] = e.error
                    if e.error_description:
                        error["description"] = e.error_description
                else:
                    error["raw_error"] = str(e)
                access.set_resource_error(resource, error)
        return access

    async def build_access(
        self, identity: KeycardIdentity | None, resources: list[str]
    ) -> AccessContext:
        """Acquire for `resources` under `identity`; never raises for a grant.

        No identity records `missing_identity`, an expired JWT subject token
        records `subject_token_expired` without an exchange, `as_self` routes
        to the client-credentials grant, and the user-bearing patterns route
        to the shared token-exchange orchestration.
        """
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

        if identity.subject_token and subject_token_expired(identity.subject_token):
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
            return await self.grant_as_self(resources, access)

        return await exchange_tokens_for_resources(
            client=self.client(),
            resources=resources,
            subject_token=identity.subject_token or "",
            access_context=access,
            application_credential=self._credential,
            user_identifier=identity.user_identifier,
            request_scopes=self._request_scopes,
        )

    # Pause payloads

    def authorization_payload(
        self, failed: list[str], access: AccessContext
    ) -> dict[str, Any]:
        """The `authorization_required` payload for the failed resources."""
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

    def sign_in_payload(self, access: AccessContext) -> dict[str, Any]:
        """The `sign_in_required` payload, with `reason` from the context error."""
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

    def pending_pause(
        self, identity: KeycardIdentity | None, access: AccessContext
    ) -> dict[str, Any] | None:
        """The pause this AccessContext calls for, if any.

        As-itself runs never pause: there is no user to send to a sign-in or
        consent page, so failures stay on the AccessContext as errors for the
        tool (and the operator's logs) to surface.
        """
        if identity is not None and identity.as_self:
            return None
        if access.has_error() and self._sign_in_url:
            return self.sign_in_payload(access)
        failed = access.get_failed_resources()
        if failed and self._authorization_url is not None:
            return self.authorization_payload(failed, access)
        return None

    @staticmethod
    def fallback_fields(payload: dict[str, Any], tool_name: str) -> dict[str, str]:
        """The pause payload reduced to the fields failed tool output carries.

        `reason` is already on a `sign_in_required` payload; a consent payload
        has one implicit kind of failure, so it reads as `consent_required`.
        """
        kind = str(payload["type"])
        return {
            "kind": kind,
            "reason": str(
                payload.get(
                    "reason",
                    "consent_required" if kind == "authorization_required" else kind,
                )
            ),
            "url": str(payload.get("sign_in_url") or payload.get("authorization_url")),
            "tool": tool_name,
        }

    @staticmethod
    def fallback_text(fields: dict[str, str]) -> str:
        """Failed tool output standing in for a pause.

        Written for a model that must hand the URL to the user: the URL sits on
        its own line, and the instruction is to reproduce it verbatim, because
        a paraphrased or shortened authorization URL does not authorize
        anything.
        """
        action = (
            "sign in" if fields["kind"] == "sign_in_required" else "grant this access"
        )
        return (
            f"{fields['kind']}: the tool {fields['tool']} cannot run yet "
            f"(reason: {fields['reason']}).\n"
            f"{fields['url']}\n"
            f"Tell the user to open the URL above to {action}. Copy it into your "
            "reply exactly as written, character for character: do not shorten it, "
            "rewrite it, wrap it in other text, or describe it in words. Then ask "
            f"the user to tell you once they are done, and call {fields['tool']} again."
        )
