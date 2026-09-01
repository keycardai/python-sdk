"""Unit tests for keycardai.temporal: no Temporal server, no zone, mint stubbed.

The interceptor chain is driven directly: ``KeycardInterceptor`` wraps a stand-in
for the rest of the chain that simply runs the activity function. The OAuth
client is replaced by a recorder so every test can assert exactly which mint was
requested and that nothing was requested when a failure is expected.

The history-hygiene proof (a real workflow under a Temporal test server) lives
in ``test_history_hygiene.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Annotated

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import ExecuteActivityInput

from keycardai import temporal as kt
from keycardai.oauth import TokenExchangeRequest
from keycardai.oauth.server import ClientSecret
from keycardai.temporal import (
    GrantConfigurationError,
    KeycardInterceptor,
    Subject,
    access,
    grant,
)

RESOURCE = "urn:test:ledger"


@grant(RESOURCE)
async def granted_activity() -> str:
    return access().access_token


@grant(RESOURCE, subject_from="approver")
async def obo_activity(approver: str) -> str:
    return access().access_token


async def plain_activity() -> str:
    access()  # must raise: no @grant, no mint
    return "unreachable"


class RecordingNext:
    """Stands in for the rest of the interceptor chain: runs the activity fn."""

    async def execute_activity(self, input: ExecuteActivityInput):
        return await input.fn(*input.args)


class StubOAuthClient:
    """Replaces keycardai.oauth.AsyncClient; records what was requested.

    Deliberately not an async context manager: the interceptor must reuse one
    client per worker, never enter a fresh one per activity.
    """

    calls: list = []
    instances: int = 0
    fail_with: Exception | None = None  # raised by every grant/exchange when set

    def __init__(self, *args, **kwargs):
        type(self).instances += 1

    async def client_credentials_grant(self, resource: str):
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append(("client_credentials", resource))
        return SimpleNamespace(access_token=f"cc-tok-{len(self.calls)}")

    async def exchange_token(self, request):
        # exchange_tokens_for_resources passes a TokenExchangeRequest object
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append(("exchange", request.subject_token, request.resource))
        return SimpleNamespace(access_token=f"obo-tok-{len(self.calls)}")

    async def impersonate(self, *, user_identifier, resource, scope=None):
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append(("impersonate", user_identifier, resource))
        return SimpleNamespace(access_token=f"imp-tok-{len(self.calls)}")


class OAuthDenial(Exception):
    """Shaped like the SDK's OAuthProtocolError: carries ``.error``."""

    def __init__(self, error: str):
        super().__init__(error)
        self.error = error
        self.error_description = None


class AssertionCredential:
    """Stands in for WorkloadIdentity/WebIdentity: no HTTP-layer auth, and the
    identity attaches inside prepare_token_exchange_request."""

    prepared: list = []

    def get_http_client_auth(self):
        return None

    def set_client_config(self, config, auth_info):
        return config

    async def prepare_token_exchange_request(
        self, client, subject_token, resource, auth_info=None
    ):
        type(self).prepared.append((subject_token, resource))
        return TokenExchangeRequest(
            subject_token=subject_token,
            resource=resource,
            subject_token_type="urn:ietf:params:oauth:token-type:access_token",
        )


@pytest.fixture
def oauth_calls(monkeypatch) -> list:
    StubOAuthClient.calls = []
    StubOAuthClient.instances = 0
    StubOAuthClient.fail_with = None
    AssertionCredential.prepared = []
    monkeypatch.setattr(kt, "AsyncClient", StubOAuthClient)
    return StubOAuthClient.calls


async def session_lookup(ref: str) -> str:
    return f"session-token-for-{ref}"


def inbound(subject_token_provider=None, credential=None):
    return KeycardInterceptor(
        "https://zone.test",
        credential=credential or ClientSecret(("id", "secret")),
        subject_token_provider=subject_token_provider,
    ).intercept_activity(RecordingNext())


def call(fn, *args) -> ExecuteActivityInput:
    return ExecuteActivityInput(fn=fn, args=args, executor=None, headers={})


# --- per-call minting and the client lifecycle ------------------------------


async def test_granted_activity_gets_a_fresh_token_per_call(oauth_calls):
    chain = inbound()
    t1 = await chain.execute_activity(call(granted_activity))
    t2 = await chain.execute_activity(call(granted_activity))
    assert (t1, t2) == ("cc-tok-1", "cc-tok-2")  # per call, never reused
    assert oauth_calls == [("client_credentials", RESOURCE)] * 2


async def test_one_client_serves_the_whole_worker(oauth_calls):
    chain = inbound()
    await chain.execute_activity(call(granted_activity))
    await chain.execute_activity(call(granted_activity))
    assert StubOAuthClient.instances == 1  # client reused; only tokens are fresh


async def test_one_client_is_shared_across_activity_interceptors(oauth_calls):
    # Temporal calls intercept_activity once per activity execution; the
    # client must belong to the worker-level interceptor, not to each chain.
    interceptor = KeycardInterceptor(
        "https://zone.test", credential=ClientSecret(("id", "secret"))
    )
    first = interceptor.intercept_activity(RecordingNext())
    second = interceptor.intercept_activity(RecordingNext())
    t1 = await first.execute_activity(call(granted_activity))
    t2 = await second.execute_activity(call(granted_activity))
    assert StubOAuthClient.instances == 1
    assert t1 != t2


async def test_ungranted_activity_passes_through_without_minting(oauth_calls):
    chain = inbound()
    with pytest.raises(RuntimeError, match="@grant"):
        await chain.execute_activity(call(plain_activity))
    assert oauth_calls == []


async def test_context_is_cleared_after_the_activity(oauth_calls):
    chain = inbound()
    await chain.execute_activity(call(granted_activity))
    with pytest.raises(RuntimeError):
        access()


async def test_context_is_cleared_when_the_activity_raises(oauth_calls):
    @grant(RESOURCE)
    async def explodes() -> None:
        raise ValueError("boom")

    chain = inbound()
    with pytest.raises(ValueError):
        await chain.execute_activity(call(explodes))
    with pytest.raises(RuntimeError):
        access()


# --- failure timing and retry classification --------------------------------


async def test_mint_failure_raises_before_the_activity_body(monkeypatch):
    ran: list = []

    @grant(RESOURCE)
    async def tracked_activity() -> None:
        ran.append(True)

    chain = inbound()

    async def failing_mint(grant, input):
        raise ConnectionError("zone unreachable")

    monkeypatch.setattr(chain, "_mint", failing_mint)
    with pytest.raises(ConnectionError):
        await chain.execute_activity(call(tracked_activity))
    assert ran == []  # fail-closed: the body never started


@pytest.mark.parametrize(
    "code", ["access_denied", "insufficient_authorization", "invalid_client"]
)
async def test_policy_denial_is_non_retryable(oauth_calls, code):
    @grant(RESOURCE, subject_from="approver")
    async def denied(approver: str) -> None:
        raise AssertionError("body must not run")

    StubOAuthClient.fail_with = OAuthDenial(code)
    chain = inbound(subject_token_provider=session_lookup)
    with pytest.raises(ApplicationError, match=code) as exc:
        await chain.execute_activity(call(denied, "alice"))
    assert exc.value.non_retryable
    assert exc.value.type == "KeycardAccessDenied"


async def test_transient_exchange_failure_stays_retryable(oauth_calls):
    @grant(RESOURCE, subject_from="approver")
    async def flaky(approver: str) -> None:
        raise AssertionError("body must not run")

    StubOAuthClient.fail_with = ConnectionError("zone unreachable")
    chain = inbound(subject_token_provider=session_lookup)
    with pytest.raises(ApplicationError, match="zone unreachable") as exc:
        await chain.execute_activity(call(flaky, "alice"))
    assert not exc.value.non_retryable
    assert exc.value.type == "KeycardMintFailed"


async def test_client_credentials_denial_is_non_retryable(oauth_calls):
    @grant(RESOURCE)
    async def denied() -> None:
        raise AssertionError("body must not run")

    StubOAuthClient.fail_with = OAuthDenial("invalid_client")
    chain = inbound()
    with pytest.raises(ApplicationError, match="invalid_client") as exc:
        await chain.execute_activity(call(denied))
    assert exc.value.non_retryable


async def test_client_credentials_transient_failure_propagates(oauth_calls):
    @grant(RESOURCE)
    async def flaky() -> None:
        raise AssertionError("body must not run")

    StubOAuthClient.fail_with = ConnectionError("zone unreachable")
    chain = inbound()
    with pytest.raises(ConnectionError):  # untouched: retry policy governs it
        await chain.execute_activity(call(flaky))


async def test_grant_configuration_error_is_listable_as_non_retryable(oauth_calls):
    # GrantConfigurationError is retryable by default; the type name is the
    # contract callers put in RetryPolicy.non_retryable_error_types.
    from temporalio.common import RetryPolicy

    policy = RetryPolicy(non_retryable_error_types=["GrantConfigurationError"])
    assert GrantConfigurationError.__name__ in policy.non_retryable_error_types


# --- on-behalf-of: the exchange path ----------------------------------------


async def test_obo_exchanges_the_users_session_token(oauth_calls):
    chain = inbound(subject_token_provider=session_lookup)
    tok = await chain.execute_activity(call(obo_activity, "alice"))
    assert tok == "obo-tok-1"
    assert oauth_calls == [("exchange", "session-token-for-alice", RESOURCE)]


async def test_obo_without_a_provider_fails_closed(oauth_calls):
    chain = inbound()
    with pytest.raises(GrantConfigurationError, match="subject_token_provider"):
        await chain.execute_activity(call(obo_activity, "alice"))
    assert oauth_calls == []


def test_obo_with_a_wrong_parameter_name_fails_at_decoration():
    with pytest.raises(GrantConfigurationError, match="no_such_param"):

        @grant(RESOURCE, subject_from="no_such_param")
        async def misdeclared(approver: str) -> None: ...


# --- credential-type polymorphism -------------------------------------------


async def test_credential_discovered_from_env(oauth_calls, monkeypatch):
    monkeypatch.setenv("KEYCARD_CLIENT_ID", "env-id")
    monkeypatch.setenv("KEYCARD_CLIENT_SECRET", "env-secret")
    chain = KeycardInterceptor("https://zone.test").intercept_activity(RecordingNext())
    tok = await chain.execute_activity(call(granted_activity))
    assert tok == "cc-tok-1"


def test_no_credential_anywhere_fails_at_construction(monkeypatch):
    for var in (
        "KEYCARD_CLIENT_ID",
        "KEYCARD_CLIENT_SECRET",
        "KEYCARD_APPLICATION_CREDENTIAL_TYPE",
        "KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError, match="credential"):
        KeycardInterceptor("https://zone.test")


def test_unknown_credential_type_fails_at_construction(monkeypatch):
    monkeypatch.delenv("KEYCARD_CLIENT_ID", raising=False)
    monkeypatch.delenv("KEYCARD_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("KEYCARD_APPLICATION_CREDENTIAL_TYPE", "web_identity")
    with pytest.raises(ValueError, match="web_identity"):
        KeycardInterceptor("https://zone.test")


def test_eks_workload_identity_discovered_from_env(oauth_calls, monkeypatch, tmp_path):
    from keycardai.oauth.server import EKSWorkloadIdentity

    token_file = tmp_path / "token"
    token_file.write_text("assertion")
    monkeypatch.delenv("KEYCARD_CLIENT_ID", raising=False)
    monkeypatch.delenv("KEYCARD_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("KEYCARD_APPLICATION_CREDENTIAL_TYPE", "eks_workload_identity")
    monkeypatch.setenv("KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE", str(token_file))
    interceptor = KeycardInterceptor("https://zone.test")
    assert isinstance(interceptor._credential, EKSWorkloadIdentity)


async def test_client_credentials_grant_requires_client_secret(oauth_calls):
    chain = inbound(credential=AssertionCredential())
    with pytest.raises(GrantConfigurationError, match="ClientSecret"):
        await chain.execute_activity(call(granted_activity))
    assert oauth_calls == []


async def test_assertion_credential_prepares_the_exchange(oauth_calls):
    chain = inbound(
        subject_token_provider=session_lookup, credential=AssertionCredential()
    )
    tok = await chain.execute_activity(call(obo_activity, "alice"))
    assert tok == "obo-tok-1"
    assert AssertionCredential.prepared == [("session-token-for-alice", RESOURCE)]


async def test_client_secret_exchange_does_not_go_through_preparation(oauth_calls):
    # ClientSecret authenticates at the HTTP layer, so the exchange request is
    # the plain RFC 8693 shape; the same @grant works for both credential kinds.
    chain = inbound(subject_token_provider=session_lookup)
    await chain.execute_activity(call(obo_activity, "alice"))
    assert oauth_calls == [("exchange", "session-token-for-alice", RESOURCE)]
    assert AssertionCredential.prepared == []


# --- identity binding strategies --------------------------------------------


@dataclass
class Order:
    amount: int
    approver_id: Annotated[str, Subject()]


@dataclass
class TwoMarks:
    a: Annotated[str, Subject()]
    b: Annotated[str, Subject()]


async def test_subject_marker_binds_the_dataclass_field(oauth_calls):
    @grant(RESOURCE)
    async def marked(order: Order) -> str:
        return access().access_token

    chain = inbound(subject_token_provider=session_lookup)
    tok = await chain.execute_activity(call(marked, Order(7, "alice")))
    assert tok == "obo-tok-1"
    assert oauth_calls == [("exchange", "session-token-for-alice", RESOURCE)]


def test_duplicate_subject_markers_fail_at_decoration():
    with pytest.raises(GrantConfigurationError, match="at most one"):

        @grant(RESOURCE)
        async def ambiguous(arg: TwoMarks) -> None: ...


@grant(RESOURCE)
async def late_marked(order: LaterOrder) -> str:
    # LaterOrder is defined below: at decoration time the hint is an
    # unresolvable forward reference, so the marker scan is deferred.
    return access().access_token


@dataclass
class LaterOrder:
    approver_id: Annotated[str, Subject()]


async def test_forward_referenced_marker_resolves_at_first_execution(oauth_calls):
    chain = inbound(subject_token_provider=session_lookup)
    tok = await chain.execute_activity(call(late_marked, LaterOrder("alice")))
    assert tok == "obo-tok-1"
    assert oauth_calls == [("exchange", "session-token-for-alice", RESOURCE)]


def test_marker_plus_subject_from_fails_at_decoration():
    with pytest.raises(GrantConfigurationError, match="pick one"):

        @grant(RESOURCE, subject_from="order.amount")
        async def double(order: Order) -> None: ...


async def test_dotted_path_reaches_into_dataclass(oauth_calls):
    @grant(RESOURCE, subject_from="order.amount")
    async def dotted(order) -> str:  # unannotated: no marker scan conflict
        return access().access_token

    @dataclass
    class Plain:
        amount: str

    chain = inbound(subject_token_provider=session_lookup)
    tok = await chain.execute_activity(call(dotted, Plain("alice")))
    assert tok == "obo-tok-1"
    assert oauth_calls == [("exchange", "session-token-for-alice", RESOURCE)]


async def test_dotted_path_reaches_into_dict(oauth_calls):
    @grant(RESOURCE, subject_from="order.approver_id")
    async def dotted(order: dict) -> str:
        return access().access_token

    chain = inbound(subject_token_provider=session_lookup)
    tok = await chain.execute_activity(call(dotted, {"approver_id": "alice"}))
    assert tok == "obo-tok-1"


async def test_broken_nested_path_fails_closed(oauth_calls):
    @grant(RESOURCE, subject_from="order.nope")
    async def dotted(order: dict) -> None: ...

    chain = inbound(subject_token_provider=session_lookup)
    with pytest.raises(GrantConfigurationError, match="nope"):
        await chain.execute_activity(call(dotted, {"approver_id": "x"}))
    assert oauth_calls == []


async def test_callable_extractor(oauth_calls):
    @grant(RESOURCE, subject_from=lambda order: order["approver_id"])
    async def extracted(order: dict) -> str:
        return access().access_token

    chain = inbound(subject_token_provider=session_lookup)
    tok = await chain.execute_activity(call(extracted, {"approver_id": "alice"}))
    assert tok == "obo-tok-1"


def test_bad_path_fails_at_decoration_despite_unresolvable_hints():
    # "Missing" is a forward reference get_type_hints cannot resolve; the path
    # validation must not be deferred along with the marker scan.
    with pytest.raises(GrantConfigurationError, match="no_such_param"):

        @grant(RESOURCE, subject_from="no_such_param")
        async def misdeclared(approver: Missing) -> None: ...  # noqa: F821


async def test_still_unresolvable_hints_fail_closed_at_execution(oauth_calls):
    # Decoration is deferred; if the hints never become resolvable the
    # deferred scan raises rather than silently treating the activity as
    # app-as-itself, and nothing is minted.
    @grant(RESOURCE)
    async def deferred(order: Missing) -> str:  # noqa: F821
        return access().access_token

    chain = inbound()
    with pytest.raises(NameError, match="Missing"):
        await chain.execute_activity(call(deferred, {"approver_id": "x"}))
    assert oauth_calls == []


async def test_dotted_path_through_wrong_container_fails_closed(oauth_calls):
    @grant(RESOURCE, subject_from="order.approver_id")
    async def dotted(order: list) -> None: ...

    chain = inbound(subject_token_provider=session_lookup)
    with pytest.raises(GrantConfigurationError, match="approver_id"):
        await chain.execute_activity(call(dotted, ["alice"]))
    assert oauth_calls == []


async def test_async_callable_extractor_fails_closed(oauth_calls):
    async def looks_up(order: dict) -> str:
        return order["approver_id"]

    @grant(RESOURCE, subject_from=looks_up)
    async def extracted(order: dict) -> None: ...

    chain = inbound(subject_token_provider=session_lookup)
    with pytest.raises(GrantConfigurationError, match="synchronous"):
        await chain.execute_activity(call(extracted, {"approver_id": "x"}))
    assert oauth_calls == []


async def test_defaulted_parameter_resolves(oauth_calls):
    @grant(RESOURCE, subject_from="approver")
    async def defaulted(approver: str = "alice") -> str:
        return access().access_token

    chain = inbound(subject_token_provider=session_lookup)
    tok = await chain.execute_activity(call(defaulted))
    assert tok == "obo-tok-1"
    assert oauth_calls == [("exchange", "session-token-for-alice", RESOURCE)]


async def test_arity_mismatch_fails_closed(oauth_calls):
    chain = inbound(subject_token_provider=session_lookup)
    with pytest.raises(GrantConfigurationError, match="obo_activity"):
        await chain.execute_activity(call(obo_activity, "alice", "extra"))
    assert oauth_calls == []


# --- impersonation ----------------------------------------------------------


async def test_impersonation_needs_no_provider(oauth_calls):
    @grant(RESOURCE, subject_from="approver", impersonate=True)
    async def absent_user(approver: str) -> str:
        return access().access_token

    chain = inbound()  # deliberately no subject_token_provider
    tok = await chain.execute_activity(call(absent_user, "alice"))
    assert tok == "imp-tok-1"
    assert oauth_calls == [("impersonate", "alice", RESOURCE)]


async def test_impersonation_never_touches_the_session_store(oauth_calls):
    async def must_not_be_called(ref: str) -> str:
        raise AssertionError("impersonation must not look up sessions")

    @grant(RESOURCE, subject_from="approver", impersonate=True)
    async def absent_user(approver: str) -> str:
        return access().access_token

    chain = inbound(subject_token_provider=must_not_be_called)
    tok = await chain.execute_activity(call(absent_user, "alice"))
    assert tok == "imp-tok-1"


async def test_impersonation_works_with_the_subject_marker(oauth_calls):
    @grant(RESOURCE, impersonate=True)
    async def absent_user(order: Order) -> str:
        return access().access_token

    chain = inbound()
    tok = await chain.execute_activity(call(absent_user, Order(1, "alice")))
    assert tok == "imp-tok-1"
    assert oauth_calls == [("impersonate", "alice", RESOURCE)]


def test_impersonate_without_subject_fails_at_decoration():
    with pytest.raises(GrantConfigurationError, match="impersonate"):

        @grant(RESOURCE, impersonate=True)
        async def missing_subject(amount: int) -> None: ...


async def test_impersonation_requires_client_secret(oauth_calls):
    @grant(RESOURCE, subject_from="approver", impersonate=True)
    async def absent_user(approver: str) -> None:
        raise AssertionError("body must not run")

    chain = inbound(credential=AssertionCredential())
    with pytest.raises(GrantConfigurationError, match="ClientSecret"):
        await chain.execute_activity(call(absent_user, "alice"))
    assert oauth_calls == []


async def test_impersonation_denial_is_non_retryable(oauth_calls):
    @grant(RESOURCE, subject_from="approver", impersonate=True)
    async def denied(approver: str) -> None:
        raise AssertionError("body must not run")

    StubOAuthClient.fail_with = OAuthDenial("access_denied")
    chain = inbound()
    with pytest.raises(ApplicationError, match="access_denied") as exc:
        await chain.execute_activity(call(denied, "alice"))
    assert exc.value.non_retryable


# --- composition with the Temporal decorators -------------------------------


def test_grant_composes_with_activity_defn():
    @grant(RESOURCE, subject_from="approver")
    @activity.defn
    async def decorated(approver: str) -> None: ...

    declared = getattr(decorated, kt._GRANT_ATTR)
    assert declared.resource == RESOURCE
    assert declared.resolve_extractor() is not None  # on-behalf-of mode
    assert declared.impersonate is False


def test_grant_returns_the_same_function_object():
    async def fn() -> None: ...

    assert grant(RESOURCE)(fn) is fn
