"""FastAPI server for agent services with OAuth middleware and delegation support."""

import logging
from importlib.metadata import version
from typing import Any

from fastapi import FastAPI, Request
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from keycardai.mcp.server.auth import AuthProvider
from keycardai.mcp.server.auth.application_credentials import ClientSecret
from keycardai.mcp.server.middleware.bearer import BearerAuthMiddleware
from keycardai.mcp.server.handlers.metadata import (
    InferredProtectedResourceMetadata,
    authorization_server_metadata,
    protected_resource_metadata,
)

from ..config import AgentServiceConfig

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


class AgentCardResponse(BaseModel):
    """Agent card response model for service discovery."""

    name: str
    description: str
    type: str
    identity: str
    capabilities: list[str]
    endpoints: dict[str, str]
    auth: dict[str, str]


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
    - POST /invoke (protected): Execute crew
    - GET /status (public): Health check

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

    # Protected endpoints wrapped with BearerAuthMiddleware
    protected_app = FastAPI(
        title=config.service_name,
        description=config.description,
        version=__version__,
    )

    @protected_app.post("/invoke", response_model=InvokeResponse)
    async def invoke_crew(request: Request, invoke_request: InvokeRequest) -> InvokeResponse:
        """Protected endpoint - executes crew with OAuth validation.

        Requires valid OAuth bearer token in Authorization header.
        Token must be scoped to this service (audience check).

        The crew is executed with the provided task/inputs, and the result
        is returned along with the updated delegation chain.

        Args:
            request: Starlette request object (contains auth info in state)
            invoke_request: Task and inputs for crew execution

        Returns:
            Crew execution result and delegation chain

        Raises:
            HTTPException: If crew execution fails or token is invalid
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

        # Validate crew factory is configured
        if not config.crew_factory:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=501,
                detail="No crew factory configured for this service",
            )

        try:
            # Set user token for delegation context (used by delegation tools)
            # This allows CrewAI tools to delegate with the user's token
            access_token = token_data.get("access_token")
            if access_token:
                try:
                    from ..integrations.crewai import set_delegation_token
                    set_delegation_token(access_token)
                except ImportError:
                    # CrewAI integration not available, skip token setting
                    pass

            # Create crew instance
            crew = config.crew_factory()

            # Prepare inputs
            if isinstance(invoke_request.task, dict):
                crew_inputs = invoke_request.task
            else:
                crew_inputs = {"task": invoke_request.task}

            # Merge additional inputs if provided
            if invoke_request.inputs:
                crew_inputs.update(invoke_request.inputs)

            # Execute crew
            # Note: crew.kickoff() is synchronous in CrewAI
            result = crew.kickoff(inputs=crew_inputs)

            # Update delegation chain
            updated_chain = delegation_chain + [config.client_id]

            return InvokeResponse(
                result=str(result),
                delegation_chain=updated_chain,
            )

        except Exception as e:
            from fastapi import HTTPException

            logger.error(f"Crew execution failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Crew execution failed: {str(e)}",
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

    # Combine public and protected apps with middleware
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
            # Protected routes (require authentication via middleware)
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
