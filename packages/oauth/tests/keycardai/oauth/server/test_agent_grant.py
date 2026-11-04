"""The shared agent grant engine, exercised with a stub client and no framework.

Adapted from keycardai-langchain's middleware suite: the same behaviors, minus
the agent loop, so the engine's contract holds for every integration built on
it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time

import pytest

from keycardai.oauth import NoneAuth, TokenResponse
from keycardai.oauth.exceptions import NetworkError, OAuthProtocolError
from keycardai.oauth.server import (
    Access,
    AccessContext,
    AgentGrantEngine,
    KeycardIdentity,
    subject_token_expired,
)
from keycardai.oauth.server.exceptions import ResourceAccessError
from keycardai.oauth.types.models import TokenExchangeRequest

RESOURCE = "https://api.example.test"
OTHER = "https://other.example.test"


def jwt_with_exp(exp: float) -> str:
    """An unsigned JWT carrying only exp; the engine never verifies."""

    def b64(part: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(part).encode())
        return raw.rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64({'exp': exp})}.sig"


class StubExchangeClient:
    """Stands in for keycardai.oauth.AsyncClient on the acquisition paths."""

    def __init__(self) -> None:
        self.exchange_calls: list[TokenExchangeRequest] = []
        self.impersonate_calls: list[dict[str, str | None]] = []
        self.self_calls: list[dict[str, str]] = []
        self.self_error: Exception | None = None
        self.denied_resources: set[str] = set()

    async def exchange_token(self, request: TokenExchangeRequest) -> TokenResponse:
        self.exchange_calls.append(request)
        if request.resource in self.denied_resources:
            raise RuntimeError("no grant for this resource yet")
        return TokenResponse(
            access_token=f"obo-token-for-{request.resource}",
            token_type="Bearer",
            expires_in=300,
        )

    async def impersonate(
        self, *, user_identifier: str, resource: str, scope: str | None = None
    ) -> TokenResponse:
        self.impersonate_calls.append(
            {"user": user_identifier, "resource": resource, "scope": scope}
        )
        return TokenResponse(
            access_token=f"impersonated-{user_identifier}-for-{resource}",
            token_type="Bearer",
        )

    async def client_credentials_grant(self, request=None, **kwargs) -> TokenResponse:
        self.self_calls.append(kwargs)
        if self.self_error is not None:
            raise self.self_error
        return TokenResponse(
            access_token=f"self-token-for-{kwargs['resource']}", token_type="Bearer"
        )


class StubAssertionCredential:
    """ApplicationCredential whose proof rides in the request body,
    the shape WorkloadIdentity and WebIdentity use."""

    def __init__(self, client_id: str | None = None) -> None:
        self.client_id = client_id

    def get_http_client_auth(self):  # noqa: ANN201
        return NoneAuth()

    def set_client_config(self, config, auth_info):  # noqa: ANN001, ANN201
        return config

    async def prepare_token_exchange_request(
        self, client, subject_token: str, resource: str, auth_info=None
    ):  # noqa: ANN001, ANN201
        return TokenExchangeRequest(
            subject_token=subject_token,
            resource=resource,
            subject_token_type="urn:ietf:params:oauth:token-type:access_token",
            client_assertion="stub-assertion",
            client_assertion_type="urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            client_id=self.client_id,
        )


def engine(stub: StubExchangeClient, **kwargs) -> AgentGrantEngine:
    kwargs.setdefault("resources", [RESOURCE])
    return AgentGrantEngine(client=stub, **kwargs)  # type: ignore[arg-type]


def build(eng: AgentGrantEngine, identity: KeycardIdentity | None, resources=None):
    return asyncio.run(eng.build_access(identity, resources or [RESOURCE]))


# Pattern routing


def test_on_behalf_of_exchanges_the_subject_token() -> None:
    stub = StubExchangeClient()
    access = build(engine(stub), Access.on_behalf_of("caller-token"))
    assert access.access(RESOURCE).access_token == f"obo-token-for-{RESOURCE}"
    assert stub.exchange_calls[0].subject_token == "caller-token"
    assert not stub.impersonate_calls and not stub.self_calls


def test_impersonation_uses_the_substitute_user_path() -> None:
    stub = StubExchangeClient()
    access = build(engine(stub), Access.impersonate("alice@example.test"))
    assert access.access(RESOURCE).access_token.startswith("impersonated-alice")
    assert stub.impersonate_calls[0]["user"] == "alice@example.test"
    assert not stub.exchange_calls


def test_as_self_uses_client_credentials_not_exchange() -> None:
    stub = StubExchangeClient()
    access = build(engine(stub), Access.as_self())
    assert access.access(RESOURCE).access_token == f"self-token-for-{RESOURCE}"
    assert stub.self_calls == [{"resource": RESOURCE}]
    assert not stub.exchange_calls and not stub.impersonate_calls


def test_request_scopes_reach_the_exchange_space_delimited() -> None:
    stub = StubExchangeClient()
    eng = engine(stub, request_scopes={RESOURCE: ["read", "write"]})
    build(eng, Access.on_behalf_of("caller-token"))
    assert stub.exchange_calls[0].scope == "read write"


def test_as_self_request_scopes_reach_the_grant() -> None:
    stub = StubExchangeClient()
    eng = engine(stub, request_scopes="read")
    build(eng, Access.as_self())
    assert stub.self_calls == [{"resource": RESOURCE, "scope": "read"}]


# Missing identity, expiry, partial success


def test_missing_identity_is_recorded_not_raised() -> None:
    stub = StubExchangeClient()
    access = build(engine(stub), None)
    error = access.get_error()
    assert error is not None and error["code"] == "missing_identity"
    assert "Access.on_behalf_of" in error["message"]
    assert not stub.exchange_calls and not stub.self_calls


def test_missing_identity_message_points_at_sign_in_when_configured() -> None:
    access = build(engine(StubExchangeClient(), sign_in_url="https://s/in"), None)
    assert access.get_error()["message"] == (  # type: ignore[index]
        "No Keycard identity for this run. Sign in to continue."
    )


def test_expired_subject_token_is_not_sent_for_exchange() -> None:
    stub = StubExchangeClient()
    access = build(engine(stub), Access.on_behalf_of(jwt_with_exp(time.time() - 60)))
    assert access.get_error()["code"] == "subject_token_expired"  # type: ignore[index]
    assert not stub.exchange_calls


def test_unexpired_jwt_subject_token_exchanges_normally() -> None:
    stub = StubExchangeClient()
    access = build(engine(stub), Access.on_behalf_of(jwt_with_exp(time.time() + 600)))
    assert access.access(RESOURCE).access_token
    assert len(stub.exchange_calls) == 1


@pytest.mark.parametrize(
    "token", ["opaque", "a.b", f"x.{base64.urlsafe_b64encode(b'[1]').decode()}.y"]
)
def test_opaque_or_malformed_tokens_are_left_to_the_zone(token: str) -> None:
    assert subject_token_expired(token) is False


def test_partial_grant_yields_token_and_resource_error_side_by_side() -> None:
    stub = StubExchangeClient()
    stub.denied_resources.add(OTHER)
    access = build(
        engine(stub, resources=[RESOURCE, OTHER]),
        Access.on_behalf_of("caller-token"),
        [RESOURCE, OTHER],
    )
    assert access.access(RESOURCE).access_token
    assert access.get_resource_error(OTHER) is not None
    assert not access.has_error()
    with pytest.raises(ResourceAccessError):
        access.access(OTHER)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (OAuthProtocolError(error="access_denied"), False),
        (NetworkError("zone unreachable"), True),
        (RuntimeError("policy denies this application self access"), True),
    ],
)
def test_as_self_failure_dict_carries_retryable(error, expected) -> None:
    """The dict has the same shape exchange_tokens_for_resources records."""
    stub = StubExchangeClient()
    stub.self_error = error
    access = build(engine(stub), Access.as_self())
    recorded = access.get_resource_error(RESOURCE)
    assert recorded is not None
    assert recorded["retryable"] is expected
    assert "Client credentials grant failed" in recorded["message"]


# Configuration and credentials


def test_zone_url_is_required_without_an_injected_client() -> None:
    with pytest.raises(ValueError, match="zone_url"):
        AgentGrantEngine(resources=[RESOURCE])


def test_client_id_and_secret_are_client_secret_shorthand() -> None:
    from keycardai.oauth.server import ClientSecret

    eng = engine(StubExchangeClient(), client_id="agent", client_secret="s3cret")
    assert isinstance(eng.credential, ClientSecret)


def test_credential_and_client_id_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="not both"):
        engine(
            StubExchangeClient(),
            application_credential=StubAssertionCredential(),
            client_id="agent",
            client_secret="s3cret",
        )


def test_credential_prepares_the_exchange_request() -> None:
    stub = StubExchangeClient()
    eng = engine(stub, application_credential=StubAssertionCredential("agent"))
    build(eng, Access.on_behalf_of("caller-token"))
    assert stub.exchange_calls[0].client_assertion == "stub-assertion"


def test_credential_assertion_reaches_the_as_self_grant() -> None:
    stub = StubExchangeClient()
    eng = engine(stub, application_credential=StubAssertionCredential("agent"))
    build(eng, Access.as_self())
    assert stub.self_calls == [
        {
            "resource": RESOURCE,
            "client_assertion": "stub-assertion",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_id": "agent",
        }
    ]


def test_credential_assertion_without_client_id_omits_it_from_as_self() -> None:
    stub = StubExchangeClient()
    eng = engine(stub, application_credential=StubAssertionCredential())
    build(eng, Access.as_self())
    assert "client_id" not in stub.self_calls[0]


# Resource and target selection


def test_tool_resources_map_overrides_per_tool() -> None:
    eng = engine(StubExchangeClient(), tool_resources={"crm": [OTHER]})
    assert eng.resources_for("crm") == [OTHER]
    assert eng.resources_for("calendar") == [RESOURCE]


def test_tool_resources_callable_falls_back_on_none() -> None:
    eng = engine(
        StubExchangeClient(),
        tool_resources=lambda name: [] if name.startswith("crm_") else None,
    )
    assert eng.resources_for("crm_search") == []
    assert eng.resources_for("calendar") == [RESOURCE]


def test_grant_target_selects_override_explicit_or_default() -> None:
    eng = engine(
        StubExchangeClient(),
        tool_resources={"crm": [OTHER]},
        fallback_identity=lambda: Access.as_self(),
    )
    assert eng.grant_target(None, "crm", None) == (Access.as_self(), [OTHER])
    assert eng.grant_target(None, None, ["x"]) == (Access.as_self(), ["x"])
    assert eng.grant_target(Access.as_self(), None, None) == (
        Access.as_self(),
        [RESOURCE],
    )
    with pytest.raises(ValueError, match="not both"):
        eng.grant_target(None, "crm", ["x"])


def test_fallback_identity_callable_is_resolved_per_call() -> None:
    seen: list[KeycardIdentity | None] = [None, Access.as_self()]
    eng = engine(StubExchangeClient(), fallback_identity=lambda: seen.pop(0))
    assert eng.resolve_fallback() is None
    assert eng.resolve_fallback() == Access.as_self()


# Access factories


def test_factories_build_the_expected_identities() -> None:
    assert Access.as_self() == KeycardIdentity(as_self=True)
    assert Access.on_behalf_of("t") == KeycardIdentity(subject_token="t")
    assert Access.impersonate("u") == KeycardIdentity(user_identifier="u")
    assert not KeycardIdentity()
    with pytest.raises(TypeError, match="namespace of factories"):
        Access()


@pytest.mark.parametrize("value", ["", "   "])
def test_factories_reject_empty_input(value: str) -> None:
    with pytest.raises(ValueError):
        Access.on_behalf_of(value)
    with pytest.raises(ValueError):
        Access.impersonate(value)


# Pause payloads


def test_sign_in_payload_carries_the_reason() -> None:
    eng = engine(StubExchangeClient(), sign_in_url="https://s/in")
    access = build(eng, Access.on_behalf_of(jwt_with_exp(time.time() - 60)))
    payload = eng.pending_pause(Access.on_behalf_of("x"), access)
    assert payload == {
        "type": "sign_in_required",
        "sign_in_url": "https://s/in",
        "reason": "subject_token_expired",
        "message": "Your session has expired. Sign in again, then resume the run.",
    }


def test_authorization_payload_resolves_the_callable_url() -> None:
    stub = StubExchangeClient()
    stub.denied_resources.add(RESOURCE)
    eng = engine(stub, authorization_url=lambda failed: f"https://a/?r={failed[0]}")
    access = build(eng, Access.on_behalf_of("caller-token"))
    payload = eng.pending_pause(Access.on_behalf_of("caller-token"), access)
    assert payload is not None
    assert payload["type"] == "authorization_required"
    assert payload["resources"] == [RESOURCE]
    assert payload["authorization_url"] == f"https://a/?r={RESOURCE}"
    assert payload["errors"][RESOURCE] is not None


def test_as_self_never_pauses_even_with_both_urls() -> None:
    stub = StubExchangeClient()
    stub.self_error = OAuthProtocolError(error="access_denied")
    eng = engine(stub, authorization_url="https://a", sign_in_url="https://s")
    access = build(eng, Access.as_self())
    assert access.get_resource_error(RESOURCE) is not None
    assert eng.pending_pause(Access.as_self(), access) is None


def test_no_pause_without_urls() -> None:
    eng = engine(StubExchangeClient())
    assert eng.pending_pause(None, build(eng, None)) is None


def test_fallback_fields_and_text_relay_the_url_verbatim() -> None:
    eng = engine(StubExchangeClient(), authorization_url="https://a/consent?x=1")
    payload = eng.authorization_payload([RESOURCE], AccessContext())
    fields = AgentGrantEngine.fallback_fields(payload, "list_events")
    assert fields == {
        "kind": "authorization_required",
        "reason": "consent_required",
        "url": "https://a/consent?x=1",
        "tool": "list_events",
    }
    text = AgentGrantEngine.fallback_text(fields)
    assert "\nhttps://a/consent?x=1\n" in text
    assert "grant this access" in text and "call list_events again" in text
    sign_in = AgentGrantEngine.fallback_fields(
        {
            "type": "sign_in_required",
            "sign_in_url": "https://s",
            "reason": "missing_identity",
        },
        "t",
    )
    assert sign_in["reason"] == "missing_identity"
    assert "sign in" in AgentGrantEngine.fallback_text(sign_in)


# Client lifecycle


def test_sync_path_runs_on_one_persistent_loop() -> None:
    eng = engine(StubExchangeClient())
    loops: list[asyncio.AbstractEventLoop] = []

    async def record() -> AccessContext:
        loops.append(asyncio.get_running_loop())
        return AccessContext()

    eng.run_sync(record())
    eng.run_sync(record())
    assert loops[0] is loops[1]
    assert loops[0] is not asyncio.new_event_loop()
    assert any(t.name == "keycard-agent-grant" for t in threading.enumerate())


def test_client_is_cached_per_event_loop_and_a_new_loop_gets_its_own() -> None:
    eng = AgentGrantEngine(zone_url="https://zone.example.test", resources=[RESOURCE])

    async def get() -> object:
        return eng.client()

    first = asyncio.run(get())
    again = eng.run_sync(_wrap(get))
    assert first is not again
    assert eng.run_sync(_wrap(get)) is again


async def _wrap(fn):  # noqa: ANN001, ANN202
    return await fn()
