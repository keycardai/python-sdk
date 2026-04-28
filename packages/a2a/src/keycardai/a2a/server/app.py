"""Starlette server for Keycard-protected A2A agent services.

Wraps a2a-sdk's standard server primitives (route factories, request
handler, executor protocol) with Keycard authentication and OAuth metadata
discovery. The customer implements a2a-sdk's native ``AgentExecutor`` and
passes an instance through ``AgentServiceConfig``; this module composes
the route factories into a Starlette app, attaches Keycard's
``AuthenticationMiddleware`` to the JSONRPC mount, and propagates the
verified ``KeycardUser`` into a2a-sdk's ``ServerCallContext`` so the
executor can read the access token for downstream delegation.
"""

import logging
from importlib.metadata import version

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from keycardai.oauth.server.credentials import ClientSecret
from keycardai.starlette import (
    AuthProvider,
    KeycardAuthBackend,
    KeycardAuthError,
    KeycardUser,
    keycard_on_error,
)
from keycardai.starlette.routers.metadata import (
    well_known_authorization_server_route,
    well_known_protected_resource_route,
)

from ..config import AgentServiceConfig

logger = logging.getLogger(__name__)

try:
    __version__ = version("keycardai-a2a")
except Exception:
    __version__ = "0.1.0"


class _EagerKeycardAuthBackend(KeycardAuthBackend):
    """KeycardAuthBackend variant that rejects anonymous requests outright.

    The base ``KeycardAuthBackend`` returns ``None`` for missing
    Authorization headers so public routes can coexist with protected ones
    in a single app. Inside the agent server's ``/a2a`` mount every path
    requires authentication, so anonymous requests must 401 immediately
    rather than fall through to the JSONRPC dispatcher.
    """

    async def authenticate(self, conn: HTTPConnection):
        if not conn.headers.get("Authorization"):
            raise KeycardAuthError("invalid_token", "No bearer token provided")
        return await super().authenticate(conn)


class _KeycardServerCallContextBuilder(DefaultServerCallContextBuilder):
    """Propagate the verified ``KeycardUser`` into a2a-sdk's ``ServerCallContext``.

    The default builder wraps ``request.user`` in a thin ``StarletteUser``
    adapter that exposes only ``is_authenticated`` and ``user_name``,
    dropping the ``access_token`` / ``client_id`` / ``scopes`` / ``zone_id``
    fields. AgentExecutors need at least the ``access_token`` for delegated
    token exchange, so we stash the full ``KeycardUser`` plus the bare
    access_token in ``ServerCallContext.state`` for executors to read via
    ``context.call_context.state["access_token"]``.
    """

    def build(self, request: Request):
        ctx = super().build(request)
        user = getattr(request, "user", None)
        if isinstance(user, KeycardUser):
            ctx.state["keycard_user"] = user
            ctx.state["access_token"] = user.access_token
        return ctx


def _build_agent_card(config: AgentServiceConfig) -> AgentCard:
    """Construct an a2a-sdk 1.x AgentCard from the service configuration."""
    skills = [
        AgentSkill(
            id=cap,
            name=cap.replace("_", " ").title(),
            description=f"{cap} capability",
            tags=[cap],
        )
        for cap in config.capabilities
    ]
    return AgentCard(
        name=config.service_name,
        description=config.description or f"{config.service_name} agent service",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=f"{config.identity_url}/a2a/jsonrpc",
                protocol_binding="jsonrpc",
                protocol_version="1.0",
            ),
        ],
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            extended_agent_card=False,
        ),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=skills,
    )


class AgentServer:
    """Agent service server with Keycard OAuth middleware.

    Thin wrapper over ``create_agent_card_server``. Useful for code that
    prefers an object-oriented entry point.

    Example:
        >>> from a2a.server.agent_execution import AgentExecutor
        >>> from keycardai.a2a import AgentServiceConfig
        >>> from keycardai.a2a.server import AgentServer
        >>>
        >>> class MyExecutor(AgentExecutor):
        ...     async def execute(self, context, event_queue): ...
        ...     async def cancel(self, context, event_queue): ...
        >>>
        >>> config = AgentServiceConfig(
        ...     service_name="My Agent",
        ...     client_id="...",
        ...     client_secret="...",
        ...     identity_url="https://my-agent.example.com",
        ...     zone_id="abc123",
        ...     agent_executor=MyExecutor(),
        ... )
        >>> server = AgentServer(config)
        >>> app = server.create_app()
    """

    def __init__(self, config: AgentServiceConfig):
        self.config = config

    def create_app(self) -> Starlette:
        return create_agent_card_server(self.config)

    def serve(self) -> None:
        serve_agent(self.config)


def create_agent_card_server(config: AgentServiceConfig) -> Starlette:
    """Create the Starlette app for the agent service.

    Endpoints:

    - ``GET /.well-known/agent-card.json`` (public): a2a-sdk's agent card discovery.
    - ``GET /.well-known/oauth-protected-resource{...}`` (public): RFC 9728 metadata.
    - ``GET /.well-known/oauth-authorization-server{...}`` (public): RFC 8414 metadata.
    - ``GET /status`` (public): health check.
    - ``POST /a2a/jsonrpc`` (Keycard-protected): A2A JSONRPC dispatcher.

    Args:
        config: Service configuration.

    Returns:
        Configured Starlette application.
    """
    client_secret = ClientSecret((config.client_id, config.client_secret))
    auth_provider = AuthProvider(
        zone_url=config.auth_server_url,
        server_name=config.service_name,
        server_url=config.identity_url,
        application_credential=client_secret,
    )
    verifier = auth_provider.get_token_verifier()

    auth_middleware = Middleware(
        AuthenticationMiddleware,
        backend=_EagerKeycardAuthBackend(verifier),
        on_error=keycard_on_error,
    )

    agent_card = _build_agent_card(config)

    request_handler = DefaultRequestHandler(
        agent_executor=config.agent_executor,
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=request_handler,
        rpc_url="/jsonrpc",
        context_builder=_KeycardServerCallContextBuilder(),
    )

    agent_card_routes = create_agent_card_routes(agent_card=agent_card)

    async def get_status(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "healthy",
                "service": config.service_name,
                "identity": config.identity_url,
                "version": __version__,
            }
        )

    app = Starlette(
        routes=[
            *agent_card_routes,
            well_known_protected_resource_route(
                issuer=config.auth_server_url,
                resource="/.well-known/oauth-protected-resource{resource_path:path}",
            ),
            well_known_authorization_server_route(
                issuer=config.auth_server_url,
                resource="/.well-known/oauth-authorization-server{resource_path:path}",
            ),
            Route("/status", get_status),
            Mount(
                "/a2a",
                routes=jsonrpc_routes,
                middleware=[auth_middleware],
            ),
        ]
    )
    return app


def serve_agent(config: AgentServiceConfig) -> None:
    """Start the agent service via uvicorn (blocking).

    Convenience runner for simple deployments. Equivalent to constructing
    a Starlette app via ``create_agent_card_server`` and handing it to
    ``uvicorn.run``.
    """
    import uvicorn

    app = create_agent_card_server(config)

    logger.info(f"Starting agent service: {config.service_name}")
    logger.info(f"Service URL: {config.identity_url}")
    logger.info(
        f"OAuth metadata: {config.identity_url}/.well-known/oauth-protected-resource"
    )
    logger.info(f"Listening on {config.host}:{config.port}")

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
    )
