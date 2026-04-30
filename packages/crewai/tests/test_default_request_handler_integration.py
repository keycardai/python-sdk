"""Wire-up smoke test for ``CrewAIExecutor`` against ``a2a-sdk``'s
``DefaultRequestHandler`` and the JSONRPC route factory.

The ``test_crewai_a2a.py`` tests cover ``CrewAIExecutor.execute`` in isolation
with mocked context and event_queue. They prove the method does the right thing
when invoked directly, but they do NOT prove that ``DefaultRequestHandler``
actually invokes our ``execute`` method when a real JSONRPC ``message/send``
request comes in. That gap is the highest-value thing to close: it verifies the
wrap-fidelity claim end-to-end.

This module instantiates the headline composition exactly the way the README
quickstart shows (``DefaultRequestHandler(agent_executor=CrewAIExecutor(...), ...)``
mounted via ``create_jsonrpc_routes``), drives it with a real JSONRPC POST
through Starlette's ``TestClient``, and asserts the crew result comes back in
the response. A stub auth backend stands in for the real Keycard verifier (real
auth is exercised in keycardai-starlette's own tests); the only thing under
test here is the wire-up between the JSONRPC dispatcher and our executor.
"""

from unittest.mock import MagicMock

import pytest

pytest.importorskip("crewai")

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from keycardai.a2a import (
    AgentServiceConfig,
    KeycardServerCallContextBuilder,
    build_agent_card_from_config,
)
from keycardai.starlette import KeycardUser
from starlette.applications import Starlette
from starlette.authentication import AuthCredentials, AuthenticationBackend
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Mount
from starlette.testclient import TestClient

from keycardai.crewai import CrewAIExecutor, _current_user_token


class _StubAuthBackend(AuthenticationBackend):
    """Stand-in for ``KeycardAuthBackend`` that always authenticates with a
    fixed ``KeycardUser``.

    Real auth is covered by keycardai-starlette's own tests. Using a real
    verifier here would require a reachable JWKS endpoint, which the test
    environment does not provide; the cost of mocking the verifier outweighs
    the value, since the verifier-to-context flow is already covered by the
    ``KeycardServerCallContextBuilder`` propagation tests in keycardai-a2a.
    What this test isolates is the JSONRPC -> DefaultRequestHandler ->
    CrewAIExecutor.execute path.
    """

    def __init__(self, access_token: str):
        self._access_token = access_token

    async def authenticate(self, conn):
        user = KeycardUser(
            access_token=self._access_token,
            client_id="caller-svc",
            zone_id="test_zone",
            resource_server_url="https://test.example.com",
            scopes=["test"],
        )
        return AuthCredentials(["authenticated"]), user


@pytest.fixture
def service_config():
    return AgentServiceConfig(
        service_name="Test Crew Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone",
        capabilities=["test"],
    )


@pytest.fixture
def crew_observation():
    """Captures what the (fake) crew was driven with, for assertion."""
    return {}


@pytest.fixture
def fake_crew_factory(crew_observation):
    def _factory():
        crew = MagicMock()

        def kickoff(inputs):
            crew_observation["inputs"] = inputs
            crew_observation["token_at_kickoff"] = _current_user_token.get()
            return "fake crew result"

        crew.kickoff.side_effect = kickoff
        return crew

    return _factory


@pytest.fixture
def app(service_config, fake_crew_factory):
    """The composition under test: DefaultRequestHandler wraps CrewAIExecutor,
    mounted via the standard a2a-sdk JSONRPC route factory, with the Keycard
    context-builder propagating the verified user into ServerCallContext.state.
    """
    executor = CrewAIExecutor(crew_factory=fake_crew_factory)
    agent_card = build_agent_card_from_config(service_config)
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    return Starlette(
        routes=[
            Mount(
                "/a2a",
                routes=create_jsonrpc_routes(
                    request_handler=request_handler,
                    rpc_url="/jsonrpc",
                    context_builder=KeycardServerCallContextBuilder(),
                ),
                middleware=[
                    Middleware(
                        AuthenticationMiddleware,
                        backend=_StubAuthBackend(access_token="bearer-test-token"),
                    ),
                ],
            ),
        ]
    )


@pytest.fixture
def client(app):
    return TestClient(app)


class TestDefaultRequestHandlerInvokesCrewAIExecutor:
    """The wrap actually wraps: DefaultRequestHandler drives CrewAIExecutor when
    a JSONRPC ``message/send`` request lands at ``/a2a/jsonrpc``.
    """

    def test_message_send_drives_crew_kickoff_with_user_input(
        self, client, crew_observation
    ):
        """The user's message text reaches ``crew.kickoff(inputs={"task": ...})``.

        This is the core wire-up assertion: a JSONRPC envelope hitting the mount
        causes ``DefaultRequestHandler`` to build a ``RequestContext``, call our
        executor's ``execute``, which calls our crew. If the crew never sees the
        text, something between the dispatcher and ``execute`` is broken.
        """
        response = client.post(
            "/a2a/jsonrpc",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "msg-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "hello world"}],
                    }
                },
            },
            headers={"A2A-Version": "1.0"},
        )

        assert response.status_code == 200, response.text
        assert crew_observation["inputs"] == {"task": "hello world"}

    def test_response_carries_crew_result(self, client):
        """The string returned by the crew comes back to the JSONRPC caller.

        ``CrewAIExecutor.execute`` enqueues a ``Message`` whose text is the
        ``str(crew.kickoff(...))`` result. ``DefaultRequestHandler`` shapes that
        into the ``SendMessageResponse`` payload. The crew result string should
        appear somewhere in the response body.
        """
        response = client.post(
            "/a2a/jsonrpc",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "msg-2",
                        "role": "ROLE_USER",
                        "parts": [{"text": "anything"}],
                    }
                },
            },
            headers={"A2A-Version": "1.0"},
        )

        assert response.status_code == 200, response.text
        assert "fake crew result" in response.text

    def test_access_token_propagates_into_crew_kickoff(
        self, client, crew_observation
    ):
        """The bearer reaches the contextvar by the time crew.kickoff runs.

        The verified bearer flows: KeycardUser (set by the auth backend) ->
        request.scope["user"] -> KeycardServerCallContextBuilder ->
        ServerCallContext.state["access_token"] -> CrewAIExecutor.execute reads
        it -> set_delegation_token writes it to the contextvar -> asyncio.to_thread
        copies the context into the worker thread -> crew.kickoff observes it.

        If any link breaks, synchronous CrewAI tools delegate without the user
        token and downstream services either reject the call or attribute it to
        the wrong identity. Worth a single assertion that the chain holds.
        """
        response = client.post(
            "/a2a/jsonrpc",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "msg-3",
                        "role": "ROLE_USER",
                        "parts": [{"text": "doesn't matter"}],
                    }
                },
            },
            headers={"A2A-Version": "1.0"},
        )

        assert response.status_code == 200, response.text
        assert crew_observation["token_at_kickoff"] == "bearer-test-token"
