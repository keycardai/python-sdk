"""FastAPI server for agent services with Keycard OAuth middleware and delegation support."""

import logging
from importlib.metadata import version
from typing import Any

from a2a.server.apps.jsonrpc import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard
from fastapi import FastAPI, Request
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from keycardai.oauth.server.credentials import ClientSecret
from keycardai.starlette import (
    AuthProvider,
    KeycardAuthBackend,
    KeycardAuthError,
    keycard_on_error,
)
from keycardai.starlette.routers.metadata import (
    well_known_authorization_server_route,
    well_known_protected_resource_route,
)

from ..config import AgentServiceConfig
from .executor_bridge import KeycardToA2AExecutorBridge

logger = logging.getLogger(__name__)

try:
    __version__ = version("keycardai-a2a")
except Exception:
    __version__ = "0.1.0"


class _EagerKeycardAuthBackend(KeycardAuthBackend):
    """KeycardAuthBackend variant that rejects anonymous requests outright.

    The base KeycardAuthBackend returns ``None`` for missing Authorization
    headers so public routes coexisting with protected ones in the same
    Starlette app stay reachable. Inside the agent server's protected mounts
    (``/a2a`` JSONRPC and ``/`` invoke), every path requires authentication,
    so anonymous requests must 401 immediately rather than fall through.
    """

    async def authenticate(self, conn: HTTPConnection):
        if not conn.headers.get("Authorization"):
            raise KeycardAuthError("invalid_token", "No bearer token provided")
        return await super().authenticate(conn)


class InvokeRequest(BaseModel):
    """Request model for crew invocation.

    Attributes:
        task: Task description or parameters for the crew
        inputs: Optional dictionary of inputs for the crew
    """

    task: str | dict[str, Any]
    inputs: dict[str, Any] | None = None


class InvokeResponse(BaseModel):
    """Response model for crew invocation.

    Attributes:
        result: Result from crew execution
        delegation_chain: List of service identities in delegation chain
    """

    result: str | dict[str, Any]
    delegation_chain: list[str]


class AgentServer:
    """Agent service server with OAuth middleware.

    This class provides a high-level interface for creating agent services
    with built-in OAuth authentication, delegation support, and service discovery.

    Example:
        >>> from keycardai.a2a import AgentServiceConfig
        >>> from keycardai.a2a.server import AgentServer
        >>>
        >>> config = AgentServiceConfig(...)
        >>> server = AgentServer(config)
        >>> app = server.create_app()
        >>>
        >>> # Run with uvicorn
        >>> import uvicorn
        >>> uvicorn.run(app, host="0.0.0.0", port=8001)
    """

    def __init__(self, config: AgentServiceConfig):
        """Initialize agent server.

        Args:
            config: Service configuration
        """
        self.config = config

    def create_app(self) -> Starlette:
        """Create Starlette application with routes and middleware.

        Returns:
            Configured Starlette application
        """
        return create_agent_card_server(self.config)

    def serve(self) -> None:
        """Start the server (blocking).

        This is a convenience method that creates the app and runs it with uvicorn.
        """
        serve_agent(self.config)


def create_agent_card_server(config: AgentServiceConfig) -> Starlette:
    """Create Starlette server for agent service with OAuth middleware.

    Creates an HTTP server with endpoints:
    - GET /.well-known/agent-card.json (public): Service discovery
    - GET /.well-known/oauth-protected-resource (public): OAuth metadata
    - GET /.well-known/oauth-authorization-server (public): Auth server metadata
    - POST /a2a/jsonrpc (protected): A2A JSONRPC endpoint
    - POST /invoke (protected): Custom Keycard invoke endpoint
    - GET /status (public): Health check

    The server supports both:
    1. A2A JSONRPC protocol (standards-compliant, event-driven)
    2. Custom /invoke endpoint (simple, direct)

    Both endpoints share OAuth middleware and use the same underlying executor.

    Args:
        config: Service configuration

    Returns:
        Starlette application instance with middleware

    Example:
        >>> from keycardai.a2a import AgentServiceConfig
        >>> from keycardai.a2a.server import create_agent_card_server
        >>>
        >>> config = AgentServiceConfig(...)
        >>> app = create_agent_card_server(config)
        >>> # Run with: uvicorn app:app --host 0.0.0.0 --port 8000
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

    a2a_executor = KeycardToA2AExecutorBridge(config.agent_executor)
    a2a_handler = DefaultRequestHandler(
        agent_executor=a2a_executor,
        task_store=InMemoryTaskStore(),
    )

    agent_card_dict = config.to_agent_card()
    a2a_agent_card = AgentCard.model_validate(agent_card_dict)

    a2a_app = A2AStarletteApplication(
        agent_card=a2a_agent_card,
        http_handler=a2a_handler,
    )

    protected_app = FastAPI(
        title=config.service_name,
        description=config.description,
        version=__version__,
    )

    @protected_app.post("/invoke", response_model=InvokeResponse)
    async def invoke_agent(request: Request, invoke_request: InvokeRequest) -> InvokeResponse:
        """Protected endpoint - executes agent with OAuth validation.

        Requires a valid OAuth bearer token in the Authorization header. The
        AuthenticationMiddleware on the mount has already verified the token
        and populated ``request.user`` (KeycardUser) and ``request.auth``
        (KeycardAuthCredentials) before this handler runs.

        The agent is executed with the provided task/inputs, and the result
        is returned along with the updated delegation chain.

        Args:
            request: Starlette request object (carries verified auth context)
            invoke_request: Task and inputs for agent execution

        Returns:
            Agent execution result and delegation chain

        Raises:
            HTTPException: If agent execution fails.
        """
        user = request.user
        caller_service = user.client_id
        access_token = user.access_token

        logger.info(f"Invoke request from service={caller_service}")

        executor = config.agent_executor

        try:
            if access_token and hasattr(executor, "set_token_for_delegation"):
                executor.set_token_for_delegation(access_token)

            result = executor.execute(
                task=invoke_request.task,
                inputs=invoke_request.inputs,
            )

            updated_chain = [config.client_id]

            return InvokeResponse(
                result=str(result),
                delegation_chain=updated_chain,
            )

        except Exception as e:
            from fastapi import HTTPException

            logger.error(f"Agent execution failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Agent execution failed: {str(e)}",
            )

    async def get_agent_card(request: Request) -> JSONResponse:
        """Public endpoint - exposes service capabilities for discovery."""
        return JSONResponse(content=config.to_agent_card())

    async def get_status(request: Request) -> JSONResponse:
        """Public endpoint - health check."""
        return JSONResponse(
            content={
                "status": "healthy",
                "service": config.service_name,
                "identity": config.identity_url,
                "version": __version__,
            }
        )

    a2a_protected_app = a2a_app.build(
        agent_card_url="/agent-card.json",
        rpc_url="/jsonrpc",
    )

    app = Starlette(
        routes=[
            Route("/.well-known/agent-card.json", get_agent_card),
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
                app=a2a_protected_app,
                middleware=[auth_middleware],
            ),
            Mount(
                "/",
                app=protected_app,
                middleware=[auth_middleware],
            ),
        ]
    )

    return app


def serve_agent(config: AgentServiceConfig) -> None:
    """Start agent service (blocking call).

    Creates Starlette app and runs it with uvicorn server.
    This is a convenience function for simple deployments.

    Args:
        config: Service configuration

    Example:
        >>> from keycardai.a2a import AgentServiceConfig
        >>> from keycardai.a2a.server import serve_agent
        >>>
        >>> config = AgentServiceConfig(...)
        >>> serve_agent(config)  # Blocks until shutdown
    """
    import uvicorn

    app = create_agent_card_server(config)

    logger.info(f"Starting agent service: {config.service_name}")
    logger.info(f"Service URL: {config.identity_url}")
    logger.info(f"Agent card: {config.agent_card_url}")
    logger.info(f"OAuth metadata: {config.identity_url}/.well-known/oauth-protected-resource")
    logger.info(f"Listening on {config.host}:{config.port}")

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
    )
