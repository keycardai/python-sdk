"""Resource tokens from the local Keycard agent.

An application running beside the Keycard CLI, under ``keycard run`` or
reaching the ``keycard agent`` daemon, obtains an access token for a named
third-party resource with no application credential of its own: it asks the
agent over a Unix socket, and the agent mints the token with the developer's
zone session once a human approves the request.

The agent speaks Connect, addressed by URL path. Its generated stubs are not
published, so the unary JSON encoding is spoken directly over httpx; the
procedure path is the wire contract.

Spec: keycard-sdk-spec, specs/delegated-access/local-agent-token-source.md.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from collections.abc import AsyncGenerator, Callable, Generator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol, runtime_checkable

import httpx

from .exceptions import ConfigError, NetworkError, OAuthError, OAuthHttpError
from .types.models import TokenResponse
from .types.oauth import TokenType

AGENT_SOCKET_ENV = "KEYCARD_SOCKET"

ISSUE_TOKEN_PATH = "/keycard.agent.v1.AgentService/IssueToken"

# The host is a placeholder; the transport dials the Unix socket.
_ISSUE_TOKEN_URL = "http://keycard-agent" + ISSUE_TOKEN_PATH

_OPERATION = "keycard agent IssueToken"

_ERROR_BODY_LIMIT = 64 * 1024

# The socket's owner is not checked, so a peer on it may stream an endless or
# decompression-amplified body.
_RESPONSE_BODY_LIMIT = 1024 * 1024

_NOT_JSON = object()


class AgentCode(str, Enum):
    """Connect error codes the Keycard agent refuses a request with.

    ``AgentError.code`` is the code as received, so it may be one this
    enumeration does not list; compare against these members to branch on a
    code that has no predicate.
    """

    PERMISSION_DENIED = "permission_denied"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    FAILED_PRECONDITION = "failed_precondition"
    UNAUTHENTICATED = "unauthenticated"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    INVALID_ARGUMENT = "invalid_argument"


_RETRYABLE_CODES = frozenset({AgentCode.RESOURCE_EXHAUSTED, AgentCode.UNAVAILABLE})


class AgentError(OAuthError):
    """The local Keycard agent refused to issue a token.

    ``code`` is the Connect error code and ``message`` the agent's
    explanation, which names the remedy where there is one. Prefer the
    ``is_*`` predicates to matching on the message.
    """

    def __init__(self, code: str, message: str = ""):
        super().__init__(
            f"keycard agent: {code}: {message}" if message else f"keycard agent: {code}"
        )
        self.code = code
        self.message = message

    @property
    def retryable(self) -> bool:
        """True for ``resource_exhausted`` and ``unavailable`` only."""
        return self.code in _RETRYABLE_CODES


class AgentResponseError(OAuthError):
    """The agent answered 200 with a response that cannot be used.

    Distinct from AgentError, which is the agent refusing. ``message`` says
    what was wrong and never includes the token. Never retryable.
    """

    def __init__(self, message: str):
        super().__init__(f"keycard agent: {message}")
        self.message = message


def _agent_code(err: BaseException | None) -> str | None:
    # Follows explicit wrapping only: ``raise ... from`` and OAuthError's
    # ``cause``, which does not set ``__cause__``. ``__context__`` is not
    # followed, since raising inside ``except AgentError`` is a new failure.
    seen: set[int] = set()
    while err is not None and id(err) not in seen:
        if isinstance(err, AgentError):
            return err.code
        seen.add(id(err))
        cause = err.__cause__
        if cause is None and isinstance(err, OAuthError):
            cause = err.cause
        err = cause
    return None


def is_declined(err: BaseException | None) -> bool:
    """Issuance was refused (``permission_denied``).

    Wider than the name: besides a human declining, this covers an expired or
    failed prompt, an authorization server that rejected the exchange, and a
    daemon holding no session for the requested ``org`` and ``zone``. Do not
    retry automatically: the agent remembers approvals but not declines, so a
    retry puts the same prompt in front of the human again.
    """
    return _agent_code(err) == AgentCode.PERMISSION_DENIED


def is_rate_limited(err: BaseException | None) -> bool:
    """The agent is bounding how often it prompts (``resource_exhausted``). Back off."""
    return _agent_code(err) == AgentCode.RESOURCE_EXHAUSTED


def is_prompter_unavailable(err: BaseException | None) -> bool:
    """The host has no way to ask a human (``failed_precondition``).

    Retrying will not help; a caller with another credential falls back to it.
    """
    return _agent_code(err) == AgentCode.FAILED_PRECONDITION


def is_authorization_required(err: BaseException | None) -> bool:
    """The developer must act first (``unauthenticated``).

    Sign in, authorize the resource interactively, or repair an unusable zone
    session or device key. ``AgentError.message`` names the command.
    """
    return _agent_code(err) == AgentCode.UNAUTHENTICATED


def is_application_not_found(err: BaseException | None) -> bool:
    """The declared ``app_id`` names no application in the zone (``not_found``)."""
    return _agent_code(err) == AgentCode.NOT_FOUND


def is_unavailable(err: BaseException | None) -> bool:
    """A service behind the agent could not be reached (``unavailable``). Transient."""
    return _agent_code(err) == AgentCode.UNAVAILABLE


@runtime_checkable
class ResourceTokenSource(Protocol):
    """Supplies an access token for a named resource.

    ``resource`` is an RFC 8707 resource indicator.
    """

    async def resource_token(
        self, resource: str, scopes: Sequence[str] | None = None
    ) -> TokenResponse: ...


class _SocketRule(str, Enum):
    OPTION = "option"
    ENV = "env"
    DEFAULT = "default"


@dataclass(frozen=True)
class _Socket:
    path: str
    rule: _SocketRule

    def describe(self) -> str:
        if self.rule is _SocketRule.OPTION:
            return f"{_OPERATION} on {self.path} (the socket option)"
        if self.rule is _SocketRule.ENV:
            return f"{_OPERATION} on {self.path} (from {AGENT_SOCKET_ENV})"
        return (
            f"{_OPERATION} on {self.path} (the keycard agent daemon's default "
            f"socket, since {AGENT_SOCKET_ENV} is not set; start the daemon with "
            "`keycard agent start`, or run this process under `keycard run`)"
        )


def _resolve_socket(socket: str | None, env: Mapping[str, str]) -> _Socket:
    """Choose the agent socket by the spec's three ordered rules.

    Transcribes the Keycard CLI's ``Config.AgentSocketPath``, the canonical
    source for the daemon's socket, except that only the environment is read,
    never ``keycard.toml``. Computes a path and touches nothing on disk.
    """
    if socket:
        return _Socket(socket, _SocketRule.OPTION)

    def var(name: str) -> str | None:
        return env.get(name) or None

    if path := var(AGENT_SOCKET_ENV):
        return _Socket(path, _SocketRule.ENV)

    if sys.platform == "win32":
        raise ConfigError(
            f"{AGENT_SOCKET_ENV} is not set, and the keycard agent daemon's "
            "default socket is a Unix socket, unavailable on Windows; pass "
            f"socket= or set {AGENT_SOCKET_ENV}"
        )

    if agent_dir := var("KEYCARD_AGENT_DIR"):
        return _Socket(os.path.join(agent_dir, "agent.sock"), _SocketRule.DEFAULT)

    if runtime := var("XDG_RUNTIME_DIR"):
        run_dir = os.path.join(runtime, "keycard")
    else:
        if xdg_state := var("XDG_STATE_HOME"):
            state_dir = os.path.join(xdg_state, "keycard")
        else:
            home = var("KEYCARD_HOME") or var("HOME")
            if home is None:
                raise ConfigError(
                    f"cannot locate the keycard agent socket: {AGENT_SOCKET_ENV} "
                    "is not set, and neither KEYCARD_HOME nor HOME is set; pass "
                    f"socket= or set {AGENT_SOCKET_ENV}"
                )
            state_dir = os.path.join(home, ".local", "state", "keycard")
        run_dir = os.path.join(state_dir, "run")
    return _Socket(os.path.join(run_dir, "agent.sock"), _SocketRule.DEFAULT)


class AgentTokenSource:
    """A ResourceTokenSource backed by the local Keycard agent.

    Construction opens no connection and does not resolve the socket; both
    happen on each ``resource_token`` call, so a changed environment takes
    effect on the next call. The first request for an approval blocks for as
    long as a human takes to answer the prompt, so no timeout is applied:
    bound the call with ``asyncio.timeout`` or cancellation. Tokens are not
    cached here; the agent caches them, and each call is one request to it.

    Args:
        socket: Path of the agent's Unix socket. Defaults to
            ``KEYCARD_SOCKET`` in ``env``, else the ``keycard agent`` daemon's
            default socket, located the way the Keycard CLI locates it from
            ``KEYCARD_AGENT_DIR``, ``XDG_RUNTIME_DIR``, ``XDG_STATE_HOME``,
            ``KEYCARD_HOME`` and ``HOME``. The socket's owner is not checked:
            pass it explicitly when its directory may be shared with other
            users.
        env: Where the socket variables are read from. Defaults to
            ``os.environ``, read on every call.
        workload: Name shown to the human in the approval prompt.
            Self-declared and unauthenticated.
        app_id: Identifier of the Keycard application the caller says it acts
            for, as set on the application, not its public id. Matched
            exactly. Self-declared and unauthenticated.
        org: Organization id. Needed by the multi-zone ``keycard agent``
            daemon; ignored by the per-session ``keycard run`` server.
        zone: Zone id, set alongside ``org``.
        clock: Wall-clock seconds since the epoch, for computing
            ``expires_in``.
    """

    def __init__(
        self,
        *,
        socket: str | None = None,
        env: Mapping[str, str] | None = None,
        workload: str = "",
        app_id: str = "",
        org: str = "",
        zone: str = "",
        clock: Callable[[], float] = time.time,
        _transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.socket = socket
        self.workload = workload
        self.app_id = app_id
        self.org = org
        self.zone = zone
        self._env = env
        self._clock = clock
        self._transport = _transport

    async def resource_token(
        self, resource: str, scopes: Sequence[str] | None = None
    ) -> TokenResponse:
        """Ask the agent for a token for ``resource`` carrying ``scopes``.

        ``resource`` and each scope are sent exactly as given.

        Raises:
            ConfigError: ``resource`` is empty, ``org`` or ``zone`` contains
                a control character, or the default socket cannot be located.
            NetworkError: The socket cannot be reached, or the connection
                failed mid-request. The message names the socket and how it
                was chosen.
            AgentError: The agent refused.
            OAuthHttpError: A non-200 response that is not a Connect error.
            AgentResponseError: A 200 response that cannot be used.
        """
        if not resource:
            raise ConfigError("resource is required")
        if isinstance(scopes, str):
            raise ConfigError("scopes must be a list of strings, not a string")
        for name, value in (("org", self.org), ("zone", self.zone)):
            if _has_control_character(value):
                raise ConfigError(f"{name} contains a control character")

        socket = _resolve_socket(
            self.socket, os.environ if self._env is None else self._env
        )

        fields: dict[str, object] = {
            "resource": resource,
            "scopes": list(scopes or ()),
            "workload": self.workload,
        }
        # An agent predating the field drops it, and an empty value is not a
        # declaration, so it is omitted rather than sent empty.
        if self.app_id:
            fields["appId"] = self.app_id
        headers = {"Connect-Protocol-Version": "1"}
        if self.org:
            headers["Keycard-Org"] = self.org
        if self.zone:
            headers["Keycard-Zone"] = self.zone

        # verify=False skips loading the CA bundle, about 9 ms per call, for
        # a plain-HTTP Unix socket on which TLS never applies.
        transport = self._transport or httpx.AsyncHTTPTransport(
            uds=socket.path, verify=False
        )
        try:
            async with (
                httpx.AsyncClient(transport=transport, timeout=None) as client,
                client.stream(
                    "POST", _ISSUE_TOKEN_URL, json=fields, headers=headers
                ) as response,
            ):
                if response.status_code != 200:
                    raise _refusal(
                        response.status_code,
                        await _read_prefix(response, _ERROR_BODY_LIMIT),
                    )
                content = await _read_prefix(response, _RESPONSE_BODY_LIMIT + 1)
                if len(content) > _RESPONSE_BODY_LIMIT:
                    raise AgentResponseError("response is too large")
        except httpx.TransportError as err:
            raise NetworkError(err, operation=socket.describe()) from err

        return self._token(content)

    def _token(self, content: bytes) -> TokenResponse:
        # Raised outside the except block: the decode error holds the whole
        # document, which may contain the token, and `from None` would still
        # leave it reachable as __context__.
        try:
            body = json.loads(content)
        except ValueError:
            body = _NOT_JSON
        if body is _NOT_JSON:
            raise AgentResponseError("response is not JSON")
        if not isinstance(body, dict):
            raise AgentResponseError("response is not a JSON object")

        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise AgentResponseError("response has no token")
        # The token may be an opaque third-party secret headed for an
        # Authorization header, where a control character splits or ends it.
        if _has_control_character(token):
            raise AgentResponseError("token contains a control character")

        expires_in = None
        expires_at = body.get("expiresAt")
        if expires_at is not None:
            seconds, nanos = _parse_rfc3339(expires_at)
            # Whole seconds are subtracted before the fraction is added: an
            # epoch timestamp as a float has no room for nanoseconds.
            expires_in = max(0, math.floor(seconds - self._clock() + nanos / 1e9))

        granted = body.get("scopes")
        # Absent, empty, or not a list of strings means the grant is unknown,
        # so it is never filled in from the request, and a usable token is
        # not discarded over it.
        if not (
            isinstance(granted, list)
            and granted
            and all(isinstance(s, str) for s in granted)
        ):
            granted = None

        return TokenResponse(
            access_token=token,
            token_type=TokenType.BEARER,
            expires_in=expires_in,
            scope=granted,
        )


def _has_control_character(value: str) -> bool:
    """A C0 control or DEL, any of which splits or ends an HTTP header value."""
    return any(c < " " or c == "\x7f" for c in value)


async def _read_prefix(response: httpx.Response, limit: int) -> bytes:
    prefix = bytearray()
    async for chunk in response.aiter_bytes():
        prefix += chunk
        if len(prefix) >= limit:
            break
    return bytes(prefix[:limit])


def _refusal(status: int, prefix: bytes) -> OAuthError:
    try:
        body = json.loads(prefix)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return OAuthHttpError(status, operation=_OPERATION)
    code = body.get("code")
    if not isinstance(code, str) or not code:
        return OAuthHttpError(status, operation=_OPERATION)
    message = body.get("message")
    return AgentError(code, message if isinstance(message, str) else "")


# protojson renders a Timestamp with up to nine fractional digits, beyond
# datetime's microseconds on every Python version, and before 3.11
# fromisoformat also rejects a "Z" suffix. The pattern accepts the wider RFC
# 3339 grammar, offsets included; re.ASCII keeps \d to the digits RFC 3339
# allows, since int() would accept any Unicode decimal.
_RFC3339 = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?"
    r"(?:([Zz])|([+-])(\d{2}):(\d{2}))",
    re.ASCII,
)


def _parse_rfc3339(value: object) -> tuple[int, int]:
    """Parse ``value`` into whole epoch seconds and nanoseconds."""
    match = _RFC3339.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise AgentResponseError(f"expiresAt {value!r} is not an RFC 3339 timestamp")
    year, month, day, hour, minute, second, fraction, z, sign, off_h, off_m = (
        match.groups()
    )
    try:
        offset = timedelta(0)
        if not z:
            offset = timedelta(hours=int(off_h), minutes=int(off_m))
            if sign == "-":
                offset = -offset
        when = datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
            tzinfo=timezone(offset),
        )
    except ValueError:
        raise AgentResponseError(
            f"expiresAt {value!r} is not an RFC 3339 timestamp"
        ) from None
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (when - epoch) // timedelta(seconds=1), int((fraction or "0").ljust(9, "0"))


class ResourceAuth(httpx.Auth):
    """Attaches a resource token to each request an ``httpx.AsyncClient`` sends.

    The spec's resource transport. It binds one source to one resource and
    its scopes and never derives a resource from the request URL; a client
    calling several resources uses one instance per resource. Each request
    gets ``Authorization: Bearer <token>``, replacing any existing value.
    Errors from the source propagate unwrapped, so the ``is_*`` predicates
    apply, and the request is not sent. No timeout is added: obtaining the
    token can block on a human.

    Works with ``httpx.AsyncClient`` only. A synchronous ``httpx.Client``
    raises ConfigError rather than sending the request unauthenticated.
    """

    def __init__(
        self,
        source: ResourceTokenSource,
        resource: str,
        scopes: Sequence[str] | None = None,
    ):
        if source is None:
            raise ConfigError("ResourceAuth requires a source")
        if not resource:
            raise ConfigError("ResourceAuth requires a resource")
        if isinstance(scopes, str):
            raise ConfigError("scopes must be a list of strings, not a string")
        self.source = source
        self.resource = resource
        self.scopes = list(scopes or ())

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self.source.resource_token(self.resource, self.scopes)
        request.headers["Authorization"] = f"Bearer {token.access_token}"
        yield request

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        # httpx.Auth's default sync flow passes the request through untouched,
        # which would send it unauthenticated.
        raise ConfigError("ResourceAuth requires httpx.AsyncClient")
        yield request  # pragma: no cover
