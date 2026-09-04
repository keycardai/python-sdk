"""Per-call Keycard token minting for Temporal Python workers.

An activity declares the resource it needs with ``@grant``. The worker's
:class:`KeycardInterceptor` mints a fresh token for every activity execution
through keycardai-oauth, and :func:`access` returns it inside the activity.
A mint failure raises before the activity body runs; the activity retry
policy governs what happens next.

Three identity modes:

- ``@grant(resource)``: the application acts as itself (client credentials).
- On-behalf-of: the activity input carries an identity reference (a user id,
  never a token); the interceptor's ``subject_token_provider`` returns that
  user's current session token, and an RFC 8693 exchange turns it into a
  token for ``resource``. The reference is located by one of:

  - a ``Subject()`` marker on a dataclass field:
    ``approver_id: Annotated[str, Subject()]`` (validated at decoration time),
  - ``subject_from="order.approver_id"``: a parameter name or dotted path
    into it (the top-level name is validated at decoration time), or
  - ``subject_from=lambda order: order.approver_id``: a callable receiving
    the activity's arguments (escape hatch; fails only at execution time).

- ``@grant(resource, subject_from=..., impersonate=True)``: impersonation,
  for workflows that outlive the user's session. The located value is a
  stable user identifier sent directly to the zone (no session lookup, no
  ``subject_token_provider``), which mints a short-lived substitute-user
  token. Privileged and policy-gated; forbidden by default in the zone.

The token lives only in one execution's context. Nothing here writes to
activity headers, arguments, or return values, because workflow history is
durable and replayable and must never contain credentials. Signals and
activity arguments carry identity references; tokens are minted at the edge.
"""

from __future__ import annotations

import contextvars
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, fields
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from temporalio import workflow
from temporalio.exceptions import ApplicationError
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    Interceptor,
)

# keycardai imports httpx, which the workflow sandbox restricts on import
# validation. Passing them through here (as Temporal's sentry sample does)
# lets workflow-defining files import this package normally.
with workflow.unsafe.imports_passed_through():
    from keycardai.oauth import PERMANENT_ERROR_CODES, AsyncClient, TokenResponse
    from keycardai.oauth.server import (
        AccessContext,
        ApplicationCredential,
        ClientSecret,
        discover_credential,
        exchange_tokens_for_resources,
    )
    from keycardai.oauth.server.exceptions import (
        CredentialDiscoveryError,
        ResourceAccessError,
    )

__all__ = [
    "AccessContext",
    "GrantConfigurationError",
    "KeycardInterceptor",
    "ResourceAccessError",
    "Subject",
    "access",
    "grant",
]


class GrantConfigurationError(RuntimeError):
    """A ``@grant`` declaration and the worker configuration disagree.

    Retryable by default: a worker redeploy with the fix lets the next retry
    succeed. To fail fast instead, list ``"GrantConfigurationError"`` in the
    activity retry policy's ``non_retryable_error_types``.
    """


_GRANT_ATTR = "__keycard_grant__"

SubjectTokenProvider = Callable[[str], Awaitable[str]]
"""Returns the current session token for an identity reference.

Application-supplied, typically a session-store lookup. Called at activity
execution time so a token revoked mid-workflow is never replayed from state.
"""


class Subject:
    """Marks the dataclass field that carries the user identity reference.

    Usage: ``approver_id: Annotated[str, Subject()]``. With exactly one
    marked field on an activity's dataclass argument, ``subject_from`` can
    be omitted entirely.
    """


_Extractor = Callable[[ExecuteActivityInput], Any]


@dataclass(frozen=True)
class _Grant:
    resource: str
    # Memoized: returns the identity-reference extractor, or None for
    # app-as-itself. Usually resolved at decoration time; resolution is
    # deferred to first execution only when type hints hold forward
    # references that cannot be resolved yet.
    resolve_extractor: Callable[[], _Extractor | None]
    # Impersonation: the extracted value is a stable user identifier and is
    # sent as-is (no session lookup); the zone mints a substitute-user token.
    # For workflows that resume after the user's session has ended.
    impersonate: bool = False


def _memoized(
    build: Callable[[], _Extractor | None],
) -> Callable[[], _Extractor | None]:
    cell: list = []

    def resolve() -> _Extractor | None:
        if not cell:
            cell.append(build())
        return cell[0]

    return resolve


def _bind(fn: Callable, sig: inspect.Signature, input: ExecuteActivityInput):
    try:
        bound = sig.bind(*input.args)
    except TypeError as e:
        raise GrantConfigurationError(f"{fn.__qualname__}: {e}") from None
    bound.apply_defaults()
    return bound


def _marker_extractor(fn: Callable, sig: inspect.Signature) -> _Extractor | None:
    """Find the one Subject-marked dataclass field; None means no marker.

    Raises NameError when type hints hold unresolvable forward references.
    """
    hints = get_type_hints(fn, include_extras=True)
    marked: list[tuple[str, str]] = []
    for pname in sig.parameters:
        ptype = hints.get(pname)
        if not hasattr(ptype, "__dataclass_fields__"):
            continue
        field_hints = get_type_hints(ptype, include_extras=True)
        for f in fields(ptype):
            h = field_hints.get(f.name)
            if get_origin(h) is Annotated and any(
                isinstance(m, Subject) or m is Subject for m in get_args(h)[1:]
            ):
                marked.append((pname, f.name))
    if not marked:
        return None
    if len(marked) > 1:
        raise GrantConfigurationError(
            f"{fn.__qualname__}: expected at most one Subject-marked field, "
            f"got {marked}."
        )
    pname, fname = marked[0]
    return lambda input: getattr(_bind(fn, sig, input).arguments[pname], fname)


def _path_extractor(fn: Callable, sig: inspect.Signature, path: str) -> _Extractor:
    """Dotted-path extractor; the no-dot case is a plain parameter name."""
    head, _, rest = path.partition(".")
    if head not in sig.parameters:
        raise GrantConfigurationError(
            f"@grant(subject_from={path!r}) names a parameter "
            f"{fn.__qualname__} does not have."
        )

    def extract(input: ExecuteActivityInput) -> Any:
        value = _bind(fn, sig, input).arguments[head]
        for part in rest.split(".") if rest else []:
            try:
                value = value[part] if isinstance(value, dict) else getattr(value, part)
            except (AttributeError, KeyError, TypeError):
                raise GrantConfigurationError(
                    f"@grant(subject_from={path!r}) broke at {part!r} on "
                    f"{type(value).__name__} in {fn.__qualname__}."
                ) from None
        return value

    return extract


def _callable_extractor(fn: Callable, subject_from: Callable) -> _Extractor:
    def extract(input: ExecuteActivityInput) -> Any:
        value = subject_from(*input.args)
        if inspect.iscoroutine(value):
            value.close()
            raise GrantConfigurationError(
                f"{fn.__qualname__}: the subject_from callable must be "
                "synchronous; it returned a coroutine."
            )
        return value

    return extract


def _build_extractor(
    fn: Callable, subject_from: str | Callable | None
) -> _Extractor | None:
    """Resolve the identity-binding strategy for one activity.

    Raises GrantConfigurationError on a bad declaration, NameError when
    forward references prevent deciding (the caller defers to first
    execution).
    """
    sig = inspect.signature(fn)
    if subject_from is None:
        return _marker_extractor(fn, sig)
    # Build the explicit binding first: its validation (a bad parameter name)
    # needs no type hints, so it must fail at decoration time even when the
    # marker-conflict check below cannot resolve hints yet.
    if callable(subject_from):
        extractor = _callable_extractor(fn, subject_from)
    else:
        extractor = _path_extractor(fn, sig, subject_from)
    if _marker_extractor(fn, sig) is not None:
        raise GrantConfigurationError(
            f"{fn.__qualname__} has both subject_from and a Subject marker; pick one."
        )
    return extractor


# The SDK AccessContext holding this execution's minted token, plus the
# granted resource so access() stays zero-argument.
_ctx: contextvars.ContextVar[tuple[AccessContext, str]] = contextvars.ContextVar(
    "keycard_access"
)


def grant(
    resource: str,
    subject_from: str | Callable | None = None,
    impersonate: bool = False,
) -> Callable:
    """Declare the resource an activity needs a token for.

    With no ``subject_from`` and no ``Subject()`` marker, the application
    acts as itself. Otherwise the located value is the identity reference of
    the user to act on behalf of: a ``Subject()``-marked dataclass field, a
    parameter name or dotted path (``"order.approver_id"``), or a callable
    over the activity's arguments. Bad names and duplicate markers fail at
    decoration time. Apply outermost, above ``@activity.defn``.

    With ``impersonate=True`` the located value is a stable user identifier
    (an email or oid, not a session reference): no session lookup runs and no
    ``subject_token_provider`` is needed. Use it when the workflow outlives
    the user's session. Impersonation is a privileged, policy-gated operation;
    see the README for what the zone must allow.
    """

    def deco(fn: Callable) -> Callable:
        def build() -> _Extractor | None:
            extractor = _build_extractor(fn, subject_from)
            if impersonate and extractor is None:
                raise GrantConfigurationError(
                    f"{fn.__qualname__}: impersonate=True requires "
                    "subject_from or a Subject() marker to locate the user "
                    "identifier."
                )
            return extractor

        try:
            extractor = build()
            resolve = lambda: extractor  # noqa: E731 - resolved eagerly
        except NameError:
            # Forward references not resolvable yet (PEP 563): validate at
            # first execution instead of decoration.
            resolve = _memoized(build)
        setattr(fn, _GRANT_ATTR, _Grant(resource, resolve, impersonate))
        return fn

    return deco


def access() -> TokenResponse:
    """Return the token minted for this call, inside a ``@grant`` activity.

    Never put the response (or ``.access_token``) in an activity's return
    value, an argument, or a signal: those land in durable workflow history.
    """
    try:
        ctx, resource = _ctx.get()
    except LookupError:
        raise RuntimeError(
            "No Keycard access in context. Is the activity decorated with "
            "@grant and the worker running KeycardInterceptor?"
        ) from None
    return ctx.access(resource)


class KeycardInterceptor(Interceptor):
    """Worker interceptor that mints a fresh Keycard token per activity execution.

    Args:
        zone_url: Keycard zone issuer URL (``https://<zone-id>.keycard.cloud``).
            Token endpoints are discovered from it.
        credential: How this application authenticates to the zone:
            ``ClientSecret``, ``WebIdentity``, or ``WorkloadIdentity`` from
            ``keycardai.oauth.server``. When omitted,
            :func:`keycardai.oauth.server.discover_credential` builds it from
            the canonical environment variables: ``KEYCARD_CLIENT_ID`` and
            ``KEYCARD_CLIENT_SECRET`` for a client secret, a workload identity
            token file variable for workload identity, and
            ``KEYCARD_APPLICATION_CREDENTIAL_TYPE`` (``client_secret`` or
            ``workload_identity``) to choose when the environment can build
            more than one.
        subject_token_provider: Resolves an identity reference to that user's
            current session token. Required for on-behalf-of activities
            (``subject_from=...`` or a ``Subject()`` marker); not used by
            app-as-itself or ``impersonate=True`` grants.

    Raises:
        GrantConfigurationError: ``credential`` was omitted and the
            environment describes no credential, an incomplete one, or an
            ambiguous set that ``KEYCARD_APPLICATION_CREDENTIAL_TYPE`` does
            not choose between.
    """

    def __init__(
        self,
        zone_url: str,
        credential: ApplicationCredential | None = None,
        subject_token_provider: SubjectTokenProvider | None = None,
    ) -> None:
        if credential is None:
            try:
                credential = discover_credential()
            except CredentialDiscoveryError as e:
                # Same taxonomy as every other worker-configuration problem
                # in this package, so retry policies see one error class.
                raise GrantConfigurationError(str(e)) from e
        self._credential = credential
        # One client for the worker's lifetime: endpoint discovery runs once
        # and is cached on the instance. Tokens are still minted per call;
        # Keycard's no-caching rule is about tokens, not clients.
        self._client = AsyncClient(zone_url, auth=credential.get_http_client_auth())
        self._subject_token_provider = subject_token_provider

    def intercept_activity(
        self, next: ActivityInboundInterceptor
    ) -> ActivityInboundInterceptor:
        return _KeycardActivityInboundInterceptor(
            next, self._client, self._credential, self._subject_token_provider
        )


class _KeycardActivityInboundInterceptor(ActivityInboundInterceptor):
    def __init__(
        self,
        next: ActivityInboundInterceptor,
        client: AsyncClient,
        credential: ApplicationCredential,
        subject_token_provider: SubjectTokenProvider | None,
    ) -> None:
        super().__init__(next)
        self._client = client
        self._credential = credential
        self._subject_token_provider = subject_token_provider

    async def _mint(self, grant: _Grant, input: ExecuteActivityInput) -> AccessContext:
        ctx = AccessContext()
        extractor = grant.resolve_extractor()
        if extractor is None:
            # App-as-itself. exchange_tokens_for_resources is exchange-only,
            # so client credentials stay a direct call. Assertion credentials
            # (workload/web identity) authenticate per exchange request and
            # have no SDK bridge for this path yet.
            if not isinstance(self._credential, ClientSecret):
                raise GrantConfigurationError(
                    f"{input.fn.__qualname__} uses @grant without subject_from "
                    "(client credentials), which requires a ClientSecret "
                    f"credential; the worker has {type(self._credential).__name__}."
                )
            try:
                resp = await self._client.client_credentials_grant(
                    resource=grant.resource
                )
            except Exception as e:
                code = getattr(e, "error", None)
                if code in PERMANENT_ERROR_CODES:
                    raise ApplicationError(
                        f"Keycard denied {grant.resource}: {code}",
                        type="KeycardAccessDenied",
                        non_retryable=True,
                    ) from e
                raise  # transient: the activity retry policy governs it
            ctx.set_token(grant.resource, resp)
        elif grant.impersonate:
            # client.impersonate() authenticates only at the HTTP layer, and
            # the SDK requires client-credentials auth for it. Assertion
            # credentials (workload/web identity) present NoneAuth there, so
            # the request would go out unauthenticated: fail with the real
            # reason instead of the zone's invalid_client.
            if not isinstance(self._credential, ClientSecret):
                raise GrantConfigurationError(
                    f"{input.fn.__qualname__} uses @grant(impersonate=True), "
                    "which requires a ClientSecret credential; the worker "
                    f"has {type(self._credential).__name__}."
                )
            # The extracted value is the user identifier itself; the zone
            # mints a substitute-user token. subject_token is unused on this
            # path but required by the helper's signature.
            await exchange_tokens_for_resources(
                client=self._client,
                resources=[grant.resource],
                subject_token="",
                access_context=ctx,
                user_identifier=extractor(input),
            )
            _raise_on_mint_error(ctx, grant.resource)
        else:
            subject_token = await self._resolve_subject(extractor, input)
            await exchange_tokens_for_resources(
                client=self._client,
                resources=[grant.resource],
                subject_token=subject_token,
                access_context=ctx,
                application_credential=self._credential,
            )
            # The helper records failures on the context instead of raising.
            # Surface them here so the activity still fails before its body,
            # with the right retryability.
            _raise_on_mint_error(ctx, grant.resource)
        return ctx

    async def _resolve_subject(
        self, extractor: _Extractor, input: ExecuteActivityInput
    ) -> str:
        if self._subject_token_provider is None:
            raise GrantConfigurationError(
                f"{input.fn.__qualname__} uses an on-behalf-of @grant but "
                "KeycardInterceptor has no subject_token_provider."
            )
        return await self._subject_token_provider(extractor(input))

    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        declared = getattr(input.fn, _GRANT_ATTR, None)
        if declared is None:
            return await self.next.execute_activity(input)
        token = _ctx.set((await self._mint(declared, input), declared.resource))
        try:
            return await self.next.execute_activity(input)
        finally:
            _ctx.reset(token)


def _raise_on_mint_error(ctx: AccessContext, resource: str) -> None:
    if not ctx.has_errors():
        return
    err = ctx.get_resource_error(resource) or ctx.get_error() or {}
    code = err.get("code")
    detail = err.get("description") or err.get("raw_error") or err.get("message") or ""
    # Permanent denials come from keycardai.oauth: the same set that drives
    # OAuthProtocolError.retryable (equivalence pinned in the tests).
    if code in PERMANENT_ERROR_CODES:
        raise ApplicationError(
            f"Keycard denied {resource}: {code}: {detail}",
            type="KeycardAccessDenied",
            non_retryable=True,
        )
    raise ApplicationError(
        f"Keycard mint failed for {resource}: {code or detail}",
        type="KeycardMintFailed",
    )
