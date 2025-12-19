"""FastAPI server for agent services with OAuth middleware and delegation support."""

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
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from keycardai.mcp.server.auth import AuthProvider
from keycardai.mcp.server.auth.application_credentials import ClientSecret
from keycardai.mcp.server.handlers.metadata import (
    InferredProtectedResourceMetadata,
    authorization_server_metadata,
    protected_resource_metadata,
)
from keycardai.mcp.server.middleware.bearer import BearerAuthMiddleware

from ..config import AgentServiceConfig
from .executor_bridge import KeycardToA2AExecutorBridge

logger = logging.getLogger(__name__)

# Get package version
try:
    __version__ = version("keycardai-agents")
except Exception:
    __version__ = "0.1.1"  # Fallback version


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


# Note: Using custom simple response for backward compatibility.
# The config.to_agent_card() method will return the full A2A AgentCard type.


class AgentServer:
    """Agent service server with OAuth middleware.
    
    This class provides a high-level interface for creating agent services
    with built-in OAuth authentication, delegation support, and service discovery.
    
    Example:
        >>> from keycardai.agents import AgentServiceConfig
        >>> from keycardai.agents.server import AgentServer
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
    - POST / (protected): A2A JSONRPC endpoint (message/send, message/stream, tasks/*)
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
        >>> from keycardai.agents import AgentServiceConfig
        >>> from keycardai.agents.server import create_agent_card_server
        >>>
        >>> config = AgentServiceConfig(...)
        >>> app = create_agent_card_server(config)
        >>> # Run with: uvicorn app:app --host 0.0.0.0 --port 8000
    """
    # Initialize AuthProvider for token verification
    client_secret = ClientSecret((config.client_id, config.client_secret))
    auth_provider = AuthProvider(
        zone_url=config.auth_server_url,
        mcp_server_name=config.service_name,
        mcp_server_url=config.identity_url,
        application_credential=client_secret,
    )

    # Get token verifier
    verifier = auth_provider.get_token_verifier()

    # Create A2A JSONRPC application
    # Bridge Keycard executor to A2A executor interface
    a2a_executor = KeycardToA2AExecutorBridge(config.agent_executor)
    a2a_handler = DefaultRequestHandler(
        agent_executor=a2a_executor,
        task_store=InMemoryTaskStore(),
    )

    # Convert agent card to A2A type
    agent_card_dict = config.to_agent_card()
    a2a_agent_card = AgentCard.model_validate(agent_card_dict)

    # Create A2A Starlette application
    a2a_app = A2AStarletteApplication(
        agent_card=a2a_agent_card,
        http_handler=a2a_handler,
    )

    # Protected endpoints wrapped with BearerAuthMiddleware
    protected_app = FastAPI(
        title=config.service_name,
        description=config.description,
        version=__version__,
    )

    @protected_app.post("/invoke", response_model=InvokeResponse)
    async def invoke_agent(request: Request, invoke_request: InvokeRequest) -> InvokeResponse:
        """Protected endpoint - executes agent with OAuth validation.

        Requires valid OAuth bearer token in Authorization header.
        Token must be scoped to this service (audience check).

        The agent is executed with the provided task/inputs, and the result
        is returned along with the updated delegation chain.

        Args:
            request: Starlette request object (contains auth info in state)
            invoke_request: Task and inputs for agent execution

        Returns:
            Agent execution result and delegation chain

        Raises:
            HTTPException: If agent execution fails or token is invalid
        """
        # Extract token data from request state (set by BearerAuthMiddleware)
        token_data = request.state.keycardai_auth_info

        # Extract caller identity from token
        caller_user = token_data.get("sub")  # Original user
        caller_service = token_data.get("client_id")  # Calling service (if A2A)
        delegation_chain = token_data.get("delegation_chain", [])

        logger.info(
            f"Invoke request from user={caller_user}, service={caller_service}, "
            f"chain={delegation_chain}"
        )

        # Get executor
        executor = config.agent_executor

        try:
            # Set delegation token context if executor supports it
            access_token = token_data.get("access_token")
            if access_token and hasattr(executor, "set_token_for_delegation"):
                executor.set_token_for_delegation(access_token)

            # Execute agent
            result = executor.execute(
                task=invoke_request.task,
                inputs=invoke_request.inputs,
            )

            # Update delegation chain
            updated_chain = delegation_chain + [config.client_id]

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

    # OAuth metadata endpoints (public)
    # Note: These are wrapped to match MCP metadata handler signature
    async def oauth_metadata_handler(request: Request):
        """Public endpoint - OAuth protected resource metadata.

        Returns OAuth metadata for this protected resource, enabling clients
        to discover authorization servers and OAuth endpoints.

        Args:
            request: Starlette request object

        Returns:
            Response with OAuth metadata
        """
        # Create metadata handler using configurable authorization server URL
        handler = protected_resource_metadata(
            InferredProtectedResourceMetadata(
                authorization_servers=[config.auth_server_url]
            ),
            enable_multi_zone=False,
        )

        # Call the synchronous handler (returns Response directly)
        return handler(request)

    async def auth_server_metadata_handler(request: Request):
        """Public endpoint - authorization server metadata.

        Returns authorization server metadata (well-known discovery endpoint).

        Args:
            request: Starlette request object

        Returns:
            Response with authorization server metadata
        """
        # Create metadata handler using configurable authorization server URL
        handler = authorization_server_metadata(
            config.auth_server_url,
            enable_multi_zone=False,
        )

        # Call the synchronous handler (returns Response directly)
        return handler(request)

    async def get_agent_card(request: Request) -> JSONResponse:
        """Public endpoint - exposes service capabilities for discovery.

        Returns agent card with service metadata, capabilities, and endpoints.
        This endpoint is public and does not require authentication.

        Args:
            request: Starlette request object

        Returns:
            JSONResponse with agent card
        """
        return JSONResponse(content=config.to_agent_card())

    async def get_status(request: Request) -> JSONResponse:
        """Public endpoint - health check.

        Returns service status and basic information.
        This endpoint is public and does not require authentication.

        Args:
            request: Starlette request object

        Returns:
            JSONResponse with status dictionary
        """
        return JSONResponse(
            content={
                "status": "healthy",
                "service": config.service_name,
                "identity": config.identity_url,
                "version": __version__,
            }
        )

    # Build A2A JSONRPC app with protected middleware
    # Note: A2A app provides routes for POST / (JSONRPC) and GET /.well-known/agent-card.json
    # We'll mount it at /a2a to avoid conflicts with our custom agent card
    a2a_protected_app = a2a_app.build(
        agent_card_url="/agent-card.json",  # Will be served at /a2a/agent-card.json
        rpc_url="/jsonrpc",  # Will be served at /a2a/jsonrpc
    )

    # Combine public, A2A JSONRPC (protected), and custom invoke (protected) routes
    app = Starlette(
        routes=[
            # Public routes (no authentication required)
            Route("/.well-known/agent-card.json", get_agent_card),
            Route(
                "/.well-known/oauth-protected-resource{resource_path:path}",
                oauth_metadata_handler,
                methods=["GET"],
            ),
            Route(
                "/.well-known/oauth-authorization-server{resource_path:path}",
                auth_server_metadata_handler,
                methods=["GET"],
            ),
            Route("/status", get_status),
            # A2A JSONRPC endpoints (protected)
            # POST /a2a/jsonrpc - JSONRPC methods (message/send, message/stream, tasks/*)
            # GET /a2a/agent-card.json - A2A agent card (duplicate of .well-known)
            Mount(
                "/a2a",
                app=a2a_protected_app,
                middleware=[Middleware(BearerAuthMiddleware, verifier=verifier)],
            ),
            # Custom Keycard endpoints (protected)
            # POST /invoke - Simple custom endpoint
            Mount(
                "/",
                app=protected_app,
                middleware=[Middleware(BearerAuthMiddleware, verifier=verifier)],
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
        >>> from keycardai.agents import AgentServiceConfig
        >>> from keycardai.agents.server import serve_agent
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
