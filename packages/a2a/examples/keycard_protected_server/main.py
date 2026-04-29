"""Runnable example: Keycard-protected A2A agent service from scratch.

Composes a2a-sdk's standard server primitives (route factories, request
handler, executor) with the keycardai-a2a auth wiring (EagerKeycardAuthBackend,
KeycardServerCallContextBuilder) and keycardai-starlette's OAuth metadata
routes into a single Starlette app.

Customers running an existing a2a-sdk app should NOT use the full
composition below; they should add the `Mount("/a2a", ...)` block plus
the two `well_known_*` routes to their own Starlette/FastAPI app. This
example is the all-in-one path for greenfield services.

Run:
    uvicorn main:app --host 0.0.0.0 --port 8000

or:
    python main.py
"""

import os

from a2a.server.agent_execution import AgentExecutor
from a2a.server.events.event_queue_v2 import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Mount

from keycardai.a2a import (
    AgentServiceConfig,
    EagerKeycardAuthBackend,
    KeycardServerCallContextBuilder,
    build_agent_card_from_config,
)
from keycardai.oauth.server.credentials import ClientSecret
from keycardai.starlette import AuthProvider, keycard_on_error
from keycardai.starlette.routers.metadata import (
    well_known_authorization_server_route,
    well_known_protected_resource_route,
)


class EchoExecutor(AgentExecutor):
    """Returns the user's message text back as an agent message."""

    async def execute(self, context, event_queue: EventQueue) -> None:
        text = context.get_user_input()
        # In a real executor you would call your agent runtime here. For a
        # delegated downstream call, read context.call_context.state["access_token"]
        # and use it as the subject token in keycardai.oauth's TokenExchangeRequest.
        from a2a.types import Message, MessageRole, Part

        message = Message(
            role=MessageRole.MESSAGE_ROLE_AGENT,
            parts=[Part(text=f"echoed: {text}")],
        )
        await event_queue.enqueue_event(message)

    async def cancel(self, context, event_queue: EventQueue) -> None:
        return None


def build_app(config: AgentServiceConfig, executor: AgentExecutor) -> Starlette:
    """Compose the Keycard-protected agent service Starlette app.

    The composition has four parts:

    1. ``AuthProvider`` from keycardai-starlette gives us a ``TokenVerifier``
       configured for the zone.
    2. ``EagerKeycardAuthBackend(verifier)`` wraps it for use inside
       Starlette's ``AuthenticationMiddleware``. The eager variant 401s on
       missing Authorization rather than falling through anonymous; the
       JSONRPC dispatcher has no per-route gate, so the mount-level
       middleware needs to be the gate.
    3. ``build_agent_card_from_config(config)`` produces the 1.x ``AgentCard``
       passed to both ``create_agent_card_routes`` (for discovery) and
       ``DefaultRequestHandler`` (for executor wiring).
    4. ``KeycardServerCallContextBuilder()`` propagates the verified
       ``KeycardUser`` into ``ServerCallContext.state`` so the executor
       can read ``context.call_context.state["access_token"]`` for
       delegated token exchange.
    """
    auth_provider = AuthProvider(
        zone_url=config.auth_server_url,
        server_name=config.service_name,
        server_url=config.identity_url,
        application_credential=ClientSecret((config.client_id, config.client_secret)),
    )
    verifier = auth_provider.get_token_verifier()

    agent_card = build_agent_card_from_config(config)
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    return Starlette(
        routes=[
            *create_agent_card_routes(agent_card=agent_card),
            well_known_protected_resource_route(
                issuer=config.auth_server_url,
                resource="/.well-known/oauth-protected-resource{resource_path:path}",
            ),
            well_known_authorization_server_route(
                issuer=config.auth_server_url,
                resource="/.well-known/oauth-authorization-server{resource_path:path}",
            ),
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
                        backend=EagerKeycardAuthBackend(verifier),
                        on_error=keycard_on_error,
                    ),
                ],
            ),
        ]
    )


config = AgentServiceConfig(
    service_name=os.getenv("SERVICE_NAME", "Echo Agent"),
    client_id=os.getenv("KEYCARD_CLIENT_ID", "your_client_id"),
    client_secret=os.getenv("KEYCARD_CLIENT_SECRET", "your_client_secret"),
    identity_url=os.getenv("IDENTITY_URL", "https://echo-agent.example.com"),
    zone_id=os.getenv("KEYCARD_ZONE_ID", "your_zone_id"),
    description="Echo agent: responds with the message text it received.",
    capabilities=["echo"],
)

app = build_app(config, EchoExecutor())


def run() -> None:
    """Run the agent service via uvicorn (blocking)."""
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting {config.service_name} on {host}:{port}")
    print(f"Agent card: {config.agent_card_url}")
    print(f"JSONRPC: {config.jsonrpc_url}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
