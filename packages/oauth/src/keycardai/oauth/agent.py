"""Resource tokens from the local Keycard agent.

An application running beside the Keycard CLI (under ``keycard run``, or
reaching the ``keycard agent`` daemon) can obtain an access token for a named
third-party resource without holding a client secret: it asks the CLI over the
Unix socket named by ``KEYCARD_SOCKET``, and the CLI mints the token after a
human approves the request.

The CLI speaks Connect, addressed by URL path. Its generated stubs are not
published, so the unary JSON encoding is spoken directly over httpx; the
procedure path is the wire contract and a rename on either side is a break.
"""

import asyncio
import json
import os
import re
import time
from collections.abc import AsyncGenerator, Callable, Generator, Mapping
from datetime import datetime
from typing import Protocol, runtime_checkable

import httpx

from .exceptions import ConfigError, NetworkError, OAuthError, OAuthHttpError
from .types.models import TokenResponse, TokenType

AGENT_SOCKET_ENV = "KEYCARD_SOCKET"

# The host is a placeholder; the transport dials the Unix socket.
_ISSUE_TOKEN_URL = "http://keycard-agent/keycard.agent.v1.AgentService/IssueToken"

# Sized for the request the caller is about to make, not for clock skew: the
# token comes from a local socket, so re-minting is cheap and handing out one
# that expires mid-request is the expensive outcome.
_EXPIRY_LEEWAY = 30.0

_ERROR_BODY_LIMIT = 64 * 1024


class AgentCode:
    """Connect error codes the Keycard agent refuses a request with.

    The strings are matched, never derived, so a caller can branch on a code
    that has no predicate yet.
    """

    DECLINED = "permission_denied"
    RATE_LIMITED = "resource_exhausted"
    PROMPTER_UNAVAILABLE = "failed_precondition"
    AUTHORIZATION_REQUIRED = "unauthenticated"
    INVALID_ARGUMENT = "invalid_argument"
    APPLICATION_NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


class AgentError(OAuthError):
    """A refusal from the local Keycard agent.

    ``code`` is the Connect error code. The refusals differ in what a caller
    should do about them, so prefer the ``is_*`` predicates to matching on the
    message.
    """

    def __init__(self, code: str, message: str):
        super().__init__(f"keycard agent: {code}: {message}")
        self.code = code
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.code in (AgentCode.RATE_LIMITED, AgentCode.UNAVAILABLE)


def _agent_code(err: BaseException | None) -> str:
    while err is not None:
        if isinstance(err, AgentError):
            return err.code
        err = err.__cause__
    return ""


def is_declined(err: BaseException) -> bool:
    """The request was refused and repeating it unchanged will not help.

    Wider than the name: besides a human declining, this covers an expired or
    failed prompt and an authorization server that rejected the exchange.
    ``AgentError.message`` says which.
    """
    return _agent_code(err) == AgentCode.DECLINED


def is_rate_limited(err: BaseException) -> bool:
    """Too many approval prompts; back off before retrying."""
    return _agent_code(err) == AgentCode.RATE_LIMITED


def is_prompter_unavailable(err: BaseException) -> bool:
    """The host has no way to ask a human; retrying will not help."""
    return _agent_code(err) == AgentCode.PROMPTER_UNAVAILABLE


def is_authorization_required(err: BaseException) -> bool:
    """A sign-in or one-time resource authorization is needed first.

    Wider than the name: also covers an unusable zone session or device key,
    and a declared app id the agent cannot check without a session.
    ``AgentError.message`` names the remedy.
    """
    return _agent_code(err) == AgentCode.AUTHORIZATION_REQUIRED


def is_application_not_found(err: BaseException) -> bool:
    """The declared app id names no application in the zone."""
    return _agent_code(err) == AgentCode.APPLICATION_NOT_FOUND


def is_unavailable(err: BaseException) -> bool:
    """A transient failure behind the agent; retry with backoff."""
    return _agent_code(err) == AgentCode.UNAVAILABLE


@runtime_checkable
class ResourceTokenSource(Protocol):
    """Supplies an access token for a named third-party resource.

    ``resource`` is an RFC 8707 resource indicator and is passed through
    unchanged: the authorization server resolves it, and normalizing it here
    would be a second, disagreeing opinion about what the caller asked for.
    """

    async def resource_token(self, resource: str, *scopes: str) -> TokenResponse: ...


class AgentTokenSource:
    """A ResourceTokenSource backed by the local Keycard agent.

    The first request for a resource and scope set may block for as long as a
    human takes to answer the approval prompt, so no timeout is applied; bound
    the call with ``asyncio.timeout`` if needed. The agent remembers the
    approval, not the token, so tokens are cached here until shortly before
    they expire, and concurrent first requests for the same key share one mint.

    Args:
        socket: Path of the agent's Unix socket. Defaults to ``KEYCARD_SOCKET``
            in ``env``.
        workload: Name shown to the human in the approval prompt. Self-declared
            and unauthenticated, so choose something that helps the developer
            recognise the application, not something that claims trust.
        app_id: Public identifier of the Keycard application making the
            request. The agent resolves it to the application's name and shows
            both in the prompt marked self-asserted: nothing authenticates the
            claim.
        org: Organization id. Required by the multi-zone ``keycard agent``
            daemon; the per-session server started by ``keycard run`` ignores it.
        zone: Zone id, required alongside ``org``.
        env: Environment to read ``KEYCARD_SOCKET`` from. Defaults to
            ``os.environ``.
        clock: Wall-clock seconds, for the cache's expiry checks.
    """

    def __init__(
        self,
        *,
        socket: str | None = None,
        workload: str = "",
        app_id: str = "",
        org: str = "",
        zone: str = "",
        env: Mapping[str, str] | None = None,
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
        self._cache: dict[
            tuple[str, tuple[str, ...]], tuple[TokenResponse, float | None]
        ] = {}
        self._locks: dict[tuple[str, tuple[str, ...]], asyncio.Lock] = {}

    async def resource_token(self, resource: str, *scopes: str) -> TokenResponse:
        if not resource:
            raise ConfigError("resource is required")

        key = (resource, scopes)
        if (cached := self._lookup(key)) is not None:
            return cached
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if (cached := self._lookup(key)) is not None:
                return cached
            token, expires_at = await self._mint(resource, scopes)
            self._cache[key] = (token, expires_at)
            return token

    def _lookup(self, key: tuple[str, tuple[str, ...]]) -> TokenResponse | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        token, expires_at = entry
        # No expiry means the credential has none, as with a static API key,
        # not that it already expired; re-minting it would cost a round trip
        # per call for exactly the credentials that never need refreshing.
        if expires_at is None:
            return token
        if self._clock() + _EXPIRY_LEEWAY > expires_at:
            return None
        return token

    def _socket_path(self) -> str:
        if self.socket:
            return self.socket
        env = self._env if self._env is not None else os.environ
        if path := env.get(AGENT_SOCKET_ENV):
            return path
        raise ConfigError(
            f"{AGENT_SOCKET_ENV} is not set; run this application under `keycard run`"
        )

    async def _mint(
        self, resource: str, scopes: tuple[str, ...]
    ) -> tuple[TokenResponse, float | None]:
        socket = self._socket_path()

        fields: dict[str, object] = {
            "resource": resource,
            "scopes": list(scopes),
            "workload": self.workload,
        }
        # Only when set: an agent predating the field drops it silently, and
        # an empty claim is not a claim.
        if self.app_id:
            fields["appId"] = self.app_id
        headers = {"Connect-Protocol-Version": "1"}
        if self.org:
            headers["Keycard-Org"] = self.org
        if self.zone:
            headers["Keycard-Zone"] = self.zone

        transport = self._transport or httpx.AsyncHTTPTransport(uds=socket)
        try:
            async with httpx.AsyncClient(transport=transport, timeout=None) as client:
                response = await client.post(
                    _ISSUE_TOKEN_URL, json=fields, headers=headers
                )
        except httpx.HTTPError as err:
            raise NetworkError(err, operation="keycard agent IssueToken") from err

        if response.status_code != 200:
            raise _refusal(response)

        try:
            body = response.json()
        except json.JSONDecodeError as err:
            raise OAuthError("keycard agent: decoding response", err) from err
        token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise OAuthError("keycard agent: empty token in response")
        # The token may be an opaque third-party secret headed for an
        # Authorization header, where a control character splits or ends it.
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in token):
            raise OAuthError("keycard agent: token contains a control character")

        expires_at = _parse_timestamp(body.get("expiresAt"))
        expires_in = None
        if expires_at is not None:
            expires_in = max(0, int(expires_at - self._clock()))
        return (
            TokenResponse(
                access_token=token,
                token_type=TokenType.BEARER,
                expires_in=expires_in,
                # The agent returns only a token and an expiry, so this echoes
                # what was requested, not what was granted. Read the token's
                # own scope claim when the grant matters.
                scope=list(scopes) or None,
            ),
            expires_at,
        )


def _refusal(response: httpx.Response) -> OAuthError:
    try:
        body = json.loads(response.content[:_ERROR_BODY_LIMIT])
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        body = {}
    code = body.get("code")
    if not isinstance(code, str) or not code:
        return OAuthHttpError(
            response.status_code, operation="keycard agent IssueToken"
        )
    message = body.get("message")
    return AgentError(code, message if isinstance(message, str) else "")


# protojson renders a Timestamp in RFC 3339 with up to nine fractional digits,
# which datetime.fromisoformat rejects before Python 3.11.
_FRACTION = re.compile(r"(\.\d{6})\d+")


def _parse_timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    text = _FRACTION.sub(r"\1", value)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError as err:
        raise OAuthError(f"keycard agent: malformed expiresAt {value!r}", err) from err


class ResourceAuth(httpx.Auth):
    """Attaches a resource token to each request an ``httpx.AsyncClient`` sends.

    The resource is fixed at construction rather than derived from each
    request's URL, for the same reason a ResourceTokenSource does not normalize
    it: the indicator is a name the authorization server resolves, and nothing
    requires it to match the host being called. Use one instance per resource.

    Errors from the source propagate unwrapped, so the ``is_*`` predicates
    still apply.
    """

    def __init__(self, source: ResourceTokenSource, resource: str, *scopes: str):
        if source is None:
            raise ConfigError("ResourceAuth requires a source")
        if not resource:
            raise ConfigError("ResourceAuth requires a resource")
        self.source = source
        self.resource = resource
        self.scopes = scopes

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self.source.resource_token(self.resource, *self.scopes)
        request.headers["Authorization"] = f"Bearer {token.access_token}"
        yield request

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        # httpx.Auth's default flow passes the request through untouched, which
        # would send it unauthenticated.
        raise ConfigError("ResourceAuth requires httpx.AsyncClient")
        yield request
