"""Positive-path JSONRPC test for the keycardai-a2a server primitives.

The auth-gate tests in ``test_agent_card_server.py`` only assert that the
``/a2a/jsonrpc`` mount returns 401 for anonymous requests; the body is
rejected before the dispatcher sees it. That coverage misses dispatcher-
shape drift (method-name renames, new required headers, message-envelope
field changes) because the gate fires first. This module sends a real
authenticated JSONRPC request through the dispatcher and asserts a real
response, so future a2a-sdk dispatcher changes that break our delegation
or context-builder wiring fail here rather than in customer deployments.

It pairs with ``packages/crewai/tests/test_default_request_handler_integration.py``
on the executor side; the two together cover the dispatcher contract and
the executor wrap.
"""

import pytest
from a2a.server.agent_execution import AgentExecutor
from a2a.server.events.event_queue_v2 import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import Message, Part, Role
from keycardai.starlette import KeycardUser
from starlette.applications import Starlette
from starlette.authentication import AuthCredentials, AuthenticationBackend
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Mount
from starlette.testclient import TestClient

from keycardai.a2a import (
    AgentServiceConfig,
    KeycardServerCallContextBuilder,
    build_agent_card_from_config,
)


class _StubAuthBackend(AuthenticationBackend):
    """Stand-in for ``KeycardAuthBackend`` that always authenticates with a
    fixed ``KeycardUser``.

    Real auth is covered by keycardai-starlette's own tests. The point of
    this module is the dispatcher contract, not auth verification, so a
    permissive backend that still injects a valid ``KeycardUser`` is the
    right level of isolation: the context builder still runs against a
    real user object.
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


class _EchoMessageExecutor(AgentExecutor):
    """Minimal ``AgentExecutor`` that enqueues a ``Message`` carrying the
    user's input back, plus the ``access_token`` it observed in
    ``RequestContext.call_context.state``.

    Using a real ``AgentExecutor`` (rather than mocking ``DefaultRequestHandler``)
    forces the full chain to run: dispatcher -> context_builder -> executor
    -> event_queue -> response. If any link breaks, this test fails.
    """

    async def execute(self, context, event_queue: EventQueue) -> None:
        user_input = context.get_user_input()
        call_ctx = getattr(context, "call_context", None)
        access_token = call_ctx.state.get("access_token") if call_ctx else None
        body = f"echoed: {user_input}; token: {access_token}"
        message = Message(
            message_id="resp-1",
            role=Role.ROLE_AGENT,
            parts=[Part(text=body)],
        )
        await event_queue.enqueue_event(message)

    async def cancel(self, context, event_queue: EventQueue) -> None:
        return None


@pytest.fixture
def service_config():
    return AgentServiceConfig(
        service_name="JSONRPC Dispatch Test",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone",
        capabilities=["test"],
    )


@pytest.fixture
def client(service_config):
    agent_card = build_agent_card_from_config(service_config)
    request_handler = DefaultRequestHandler(
        agent_executor=_EchoMessageExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    app = Starlette(
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
    return TestClient(app)


class TestJsonRpcDispatchPositivePath:
    """Real JSONRPC ``SendMessage`` call lands at the executor and the
    response carries the executor's enqueued message.
    """

    def test_send_message_drives_executor_and_returns_response(self, client):
        """A successful round-trip exercises every link in the chain.

        If a2a-sdk renames the JSONRPC method, drops the A2A-Version header
        requirement, changes the message envelope shape, or changes how
        DefaultRequestHandler shapes the response, this test fails. The
        keycardai-crewai integration test exercises the same chain with a
        crew on top; this one isolates the keycardai-a2a primitives so a
        regression here is attributed to the right package.
        """
        response = client.post(
            "/a2a/jsonrpc",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "req-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "ping"}],
                    }
                },
            },
            headers={"A2A-Version": "1.0"},
        )

        assert response.status_code == 200, response.text
        body = response.text
        # The executor echoed the input.
        assert "echoed: ping" in body
        # The KeycardServerCallContextBuilder propagated the access_token
        # from the auth backend's KeycardUser into ServerCallContext.state.
        assert "token: bearer-test-token" in body
