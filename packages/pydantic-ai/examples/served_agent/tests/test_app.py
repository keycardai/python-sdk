"""Hermetic test of the served agent: FastAPI TestClient, a stubbed verifier
in place of the zone's JWKS, a stub exchange client in place of the zone, and
a FunctionModel in place of the LLM. The adapter, the route, the middleware and
KeycardGrant are real."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)

from keycardai.oauth import TokenResponse
from keycardai.oauth.server import ClientSecret
from keycardai.oauth.server.verifier import AccessToken
from keycardai.oauth.types.models import TokenExchangeRequest
from keycardai.starlette import AuthProvider

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
from main import CALENDAR, build_agent, build_app  # noqa: E402


@pytest.fixture(autouse=True)
def fake_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stands in for the Calendar API. It answers with one event titled
    `token <bearer>`, so a test can see which delegated token the tool sent
    without the example itself ever exposing a token."""

    def handler(request: httpx.Request) -> httpx.Response:
        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ")
        return httpx.Response(200, json={"items": [{"summary": f"token {bearer}"}]})

    monkeypatch.setattr(main, "_http", httpx.Client(transport=httpx.MockTransport(handler)))

ZONE = "https://zone.example.test"
AGENT_RESOURCE = "https://agent.example.test"
AUTH_URL = "https://zone.example.test/consent"


class StubExchangeClient:
    def __init__(self) -> None:
        self.exchange_calls: list[TokenExchangeRequest] = []
        self.granted = True

    async def exchange_token(self, request: TokenExchangeRequest) -> TokenResponse:
        self.exchange_calls.append(request)
        if not self.granted:
            raise RuntimeError("no grant for this resource yet")
        return TokenResponse(
            access_token=f"obo-{request.subject_token}-for-{request.resource}",
            token_type="Bearer",
            expires_in=300,
        )


def call_tool_then_answer() -> FunctionModel:
    """Calls list_events once, then echoes the tool result as the answer.

    The adapter streams, so FunctionModel needs its ``stream_function`` form.
    """

    def tool_returns(messages: list[ModelMessage]) -> list[str] | None:
        returns = [
            str(p.content)
            for p in messages[-1].parts
            if getattr(p, "part_kind", "") in ("tool-return", "retry-prompt")
        ]
        return returns or None

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        returns = tool_returns(messages)
        if returns is not None:
            yield " | ".join(returns)
            return
        yield {
            0: DeltaToolCall(name="list_events", json_args="{}", tool_call_id="call-0")
        }

    return FunctionModel(stream_function=stream)


def stub_verifier() -> MagicMock:
    """Accepts any ``user-<name>`` bearer as that user, as the zone's JWKS would."""

    async def verify(token: str) -> AccessToken:
        if not token.startswith("user-"):
            raise ValueError("unknown token")
        return AccessToken(
            token=token,
            client_id="chat-ui",
            scopes=["openid"],
            sub=token.removeprefix("user-"),
            sub_profile="user",
            resource=AGENT_RESOURCE,
        )

    verifier = MagicMock()
    verifier.enable_multi_zone = False
    verifier.verify_token = AsyncMock(side_effect=verify)
    verifier.verify_token_for_zone = AsyncMock(side_effect=lambda t, z: verify(t))
    return verifier


@pytest.fixture
def zone() -> StubExchangeClient:
    return StubExchangeClient()


@pytest.fixture
def client(zone: StubExchangeClient) -> TestClient:
    auth = AuthProvider(
        zone_url=ZONE,
        application_credential=ClientSecret(("agent", "secret")),
        audience=AGENT_RESOURCE,
    )
    auth.get_token_verifier = MagicMock(return_value=stub_verifier())  # type: ignore[method-assign]
    agent = build_agent(
        call_tool_then_answer(),
        zone_url=ZONE,
        authorization_url=AUTH_URL,
        client=zone,  # type: ignore[arg-type]
    )
    return TestClient(build_app(auth, agent))


def run_input(
    *, resume: list[dict[str, Any]] | None = None, with_tool_call: bool = False
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {"id": "u1", "role": "user", "content": "What is on my calendar?"}
    ]
    if with_tool_call:
        messages.append(
            {
                "id": "a1",
                "role": "assistant",
                "toolCalls": [
                    {
                        "id": "call-0",
                        "type": "function",
                        "function": {"name": "list_events", "arguments": "{}"},
                    }
                ],
            }
        )
    body: dict[str, Any] = {
        "threadId": "t1",
        "runId": "r1" if resume is None else "r2",
        "messages": messages,
        "tools": [],
        "context": [],
        "state": {},
        "forwardedProps": {},
    }
    if resume is not None:
        body["resume"] = resume
    return body


def events(response) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def chat(client: TestClient, bearer: str, body: dict[str, Any]):
    return client.post(
        "/chat", json=body, headers={"Authorization": f"Bearer {bearer}"}
    )


def finished(evs: list[dict[str, Any]]) -> dict[str, Any]:
    return next(e for e in evs if e["type"] == "RUN_FINISHED")


def text(evs: list[dict[str, Any]]) -> str:
    return "".join(
        e.get("delta", "") for e in evs if e["type"] == "TEXT_MESSAGE_CONTENT"
    )


def test_no_bearer_gets_rfc6750_challenge(client: TestClient) -> None:
    response = client.post("/chat", json=run_input())
    assert response.status_code == 401
    challenge = response.headers["WWW-Authenticate"]
    assert challenge.startswith("Bearer")
    assert "resource_metadata=" in challenge


def test_tool_sees_a_token_exchanged_for_the_callers_subject_token(
    client: TestClient, zone: StubExchangeClient
) -> None:
    evs = events(chat(client, "user-alice", run_input()))
    assert finished(evs)["outcome"]["type"] == "success"
    assert text(evs) == f"- token obo-user-alice-for-{CALENDAR}"
    assert [c.subject_token for c in zone.exchange_calls] == ["user-alice"]
    assert zone.exchange_calls[0].resource == CALENDAR


def test_two_callers_exchange_their_own_tokens(
    client: TestClient, zone: StubExchangeClient
) -> None:
    alice = text(events(chat(client, "user-alice", run_input())))
    bob = text(events(chat(client, "user-bob", run_input())))
    assert alice == f"- token obo-user-alice-for-{CALENDAR}"
    assert bob == f"- token obo-user-bob-for-{CALENDAR}"
    assert sorted(c.subject_token for c in zone.exchange_calls) == [
        "user-alice",
        "user-bob",
    ]


def test_consent_pause_reaches_the_client_and_the_resume_runs_the_tool(
    client: TestClient, zone: StubExchangeClient
) -> None:
    zone.granted = False
    evs = events(chat(client, "user-alice", run_input()))
    outcome = finished(evs)["outcome"]
    assert outcome["type"] == "interrupt"
    (interrupt,) = outcome["interrupts"]
    assert interrupt["toolCallId"] == "call-0"
    payload = interrupt["metadata"]
    assert payload["type"] == "authorization_required"
    assert payload["authorization_url"] == AUTH_URL
    assert payload["keycard_pause"] is True
    assert "message" in payload

    zone.granted = True
    resume = [
        {
            "interruptId": interrupt["id"],
            "status": "resolved",
            "payload": {"approved": True},
        }
    ]
    evs = events(
        chat(client, "user-alice", run_input(resume=resume, with_tool_call=True))
    )
    assert finished(evs)["outcome"]["type"] == "success"
    assert text(evs) == f"- token obo-user-alice-for-{CALENDAR}"
    assert [c.subject_token for c in zone.exchange_calls] == [
        "user-alice",
        "user-alice",
    ]
