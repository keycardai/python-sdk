"""FastAPI server for agent services with Keycard authentication."""

import logging
from typing import Any
import time
from importlib.metadata import version

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from keycardai.oauth.utils.bearer import extract_bearer_token
from keycardai.oauth.utils.jwt import get_verification_key, decode_and_verify_jwt, get_claims
from keycardai.oauth import AsyncClient as OAuthClient
from keycardai.oauth.http.auth import BasicAuth

from .service_config import AgentServiceConfig

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


def create_agent_card_server(config: AgentServiceConfig) -> FastAPI:
    """Create FastAPI server for agent service.

    Creates an HTTP server with three endpoints:
    - GET /.well-known/agent-card.json (public): Service discovery
    - POST /invoke (protected): Execute crew
    - GET /status (public): Health check

    Args:
        config: Service configuration

    Returns:
        FastAPI application instance

    Example:
        >>> config = AgentServiceConfig(...)
        >>> app = create_agent_card_server(config)
        >>> # Run with: uvicorn app:app --host 0.0.0.0 --port 8000
    """
    app = FastAPI(
        title=config.service_name,
        description=config.description,
        version=__version__,
    )

    # Initialize OAuth client for token validation
    oauth_base_url = f"https://{config.zone_id}.keycard.cloud"
    oauth_client = OAuthClient(
        oauth_base_url,
        auth=BasicAuth(config.client_id, config.client_secret),
    )

    async def validate_token(request: Request) -> dict[str, Any]:
        """Validate OAuth bearer token from request.

        Extracts token from Authorization header and validates with Keycard.
        Decodes token to extract delegation chain and user information.

        Args:
            request: FastAPI request object

        Returns:
            Dictionary with token claims (sub, client_id, delegation_chain, etc.)

        Raises:
            HTTPException: If token is missing, invalid, or validation fails
        """
        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization")
        token = extract_bearer_token(auth_header)

        if not token:
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid Authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            # Construct JWKS URI from zone
            jwks_uri = f"https://{config.zone_id}.keycard.cloud/.well-known/jwks.json"

            # Fetch verification key from JWKS endpoint
            verification_key = await get_verification_key(token, jwks_uri)

            # Decode and verify JWT signature
            token_data = decode_and_verify_jwt(token, verification_key)

            # Validate expiration
            exp = token_data.get("exp")
            if exp and time.time() > exp:
                raise HTTPException(
                    status_code=401,
                    detail="Token has expired",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Validate issuer
            expected_issuer = f"https://{config.zone_id}.keycard.cloud"
            iss = token_data.get("iss")
            if iss != expected_issuer:
                raise HTTPException(
                    status_code=401,
                    detail=f"Token issuer mismatch. Expected {expected_issuer}, got {iss}",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Validate audience - token must be scoped to this service
            aud = token_data.get("aud")
            if aud:
                # Handle both string and list audiences (per RFC 7519)
                audiences = [aud] if isinstance(aud, str) else aud
                if config.identity_url not in audiences:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Token audience mismatch. Expected {config.identity_url}, got {aud}",
                    )
            else:
                raise HTTPException(
                    status_code=401,
                    detail="Token missing audience claim",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            return token_data

        except HTTPException:
            # Re-raise HTTP exceptions as-is
            raise
        except ValueError as e:
            # JWT validation errors from keycardai.oauth.utils.jwt
            logger.error(f"Token validation failed: {e}")
            raise HTTPException(
                status_code=401,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except Exception as e:
            logger.error(f"Token validation error: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail="Internal server error during authentication",
            )

    @app.get("/.well-known/agent-card.json", response_model=AgentCardResponse)
    async def get_agent_card() -> dict[str, Any]:
        """Public endpoint - exposes service capabilities for discovery.

        Returns agent card with service metadata, capabilities, and endpoints.
        This endpoint is public and does not require authentication.

        Returns:
            Agent card dictionary
        """
        return config.to_agent_card()

    @app.post("/invoke", response_model=InvokeResponse)
    async def invoke_crew(
        invoke_request: InvokeRequest,
        token_data: dict[str, Any] = Depends(validate_token),
    ) -> InvokeResponse:
        """Protected endpoint - executes crew with OAuth validation.

        Requires valid OAuth bearer token in Authorization header.
        Token must be scoped to this service (audience check).

        The crew is executed with the provided task/inputs, and the result
        is returned along with the updated delegation chain.

        Args:
            invoke_request: Task and inputs for crew execution
            token_data: Token claims from validated token

        Returns:
            Crew execution result and delegation chain

        Raises:
            HTTPException: If crew execution fails or token is invalid
        """
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
            raise HTTPException(
                status_code=501,
                detail="No crew factory configured for this service",
            )

        try:
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
            logger.error(f"Crew execution failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Crew execution failed: {str(e)}",
            )

    @app.get("/status")
    async def get_status() -> dict[str, Any]:
        """Public endpoint - health check.

        Returns service status and basic information.
        This endpoint is public and does not require authentication.

        Returns:
            Status dictionary
        """
        return {
            "status": "healthy",
            "service": config.service_name,
            "identity": config.identity_url,
            "version": __version__,
        }

    return app


def serve_agent(config: AgentServiceConfig) -> None:
    """Start agent service (blocking call).

    Creates FastAPI app and runs it with uvicorn server.
    This is a convenience function for simple deployments.

    Args:
        config: Service configuration

    Example:
        >>> config = AgentServiceConfig(...)
        >>> serve_agent(config)  # Blocks until shutdown
    """
    import uvicorn

    app = create_agent_card_server(config)

    logger.info(f"Starting agent service: {config.service_name}")
    logger.info(f"Service URL: {config.identity_url}")
    logger.info(f"Agent card: {config.agent_card_url}")
    logger.info(f"Listening on {config.host}:{config.port}")

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
    )
