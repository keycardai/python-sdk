"""Service configuration for agent services."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    SecurityScheme,
)

if TYPE_CHECKING:
    from .server.executor import AgentExecutor


@dataclass
class AgentServiceConfig:
    """Configuration for deploying an agent service with Keycard identity.

    This configuration enables an agent to be deployed as a standalone HTTP service
    with its own Keycard Application identity, capable of:
    - Serving requests via REST API
    - Exposing capabilities via agent card
    - Delegating to other agent services (A2A)
    - Using MCP tools with per-call authentication

    The service is framework-agnostic and supports any agent framework through
    the AgentExecutor protocol. For CrewAI, use CrewAIExecutor adapter.

    Args:
        service_name: Human-readable name of the service
        client_id: Keycard Application client ID (service identity)
        client_secret: Keycard Application client secret
        identity_url: Public URL where this service is accessible
        zone_id: Keycard zone identifier
        port: HTTP server port (default: 8000)
        host: Server bind address (default: "0.0.0.0")
        description: Service description for agent card discovery
        capabilities: List of capabilities this service provides
        agent_executor: Executor that runs agent tasks (AgentExecutor protocol)

    Example:
        >>> from keycardai.agents import AgentServiceConfig
        >>> from keycardai.agents.integrations.crewai import CrewAIExecutor
        >>>
        >>> config = AgentServiceConfig(
        ...     service_name="PR Analysis Service",
        ...     client_id="pr_analyzer_service",
        ...     client_secret="secret_123",
        ...     identity_url="https://pr-analyzer.example.com",
        ...     zone_id="xr9r33ga15",
        ...     description="Analyzes GitHub pull requests",
        ...     capabilities=["pr_analysis", "code_review"],
        ...     agent_executor=CrewAIExecutor(lambda: create_pr_crew())
        ... )
    """

    # Service identity (Keycard Application)
    service_name: str
    client_id: str
    client_secret: str
    identity_url: str
    zone_id: str

    # Agent implementation (required)
    agent_executor: "AgentExecutor"

    # Optional configuration
    authorization_server_url: str | None = None

    # Deployment configuration
    port: int = 8000
    host: str = "0.0.0.0"

    # Agent card metadata
    description: str = ""
    capabilities: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        # Ensure identity_url doesn't have trailing slash
        if self.identity_url.endswith("/"):
            self.identity_url = self.identity_url.rstrip("/")

        # Validate required fields
        if not self.service_name:
            raise ValueError("service_name is required")
        if not self.client_id:
            raise ValueError("client_id is required")
        if not self.client_secret:
            raise ValueError("client_secret is required")
        if not self.identity_url:
            raise ValueError("identity_url is required")
        if not self.zone_id:
            raise ValueError("zone_id is required")

        # Validate URL format
        if not self.identity_url.startswith("http://") and not self.identity_url.startswith("https://"):
            raise ValueError("identity_url must start with http:// or https://")

        # Validate port
        if not (1 <= self.port <= 65535):
            raise ValueError(f"port must be between 1 and 65535, got {self.port}")

    @property
    def agent_card_url(self) -> str:
        """Get the full URL to this service's agent card."""
        return f"{self.identity_url}/.well-known/agent-card.json"

    @property
    def invoke_url(self) -> str:
        """Get the full URL to this service's invoke endpoint."""
        return f"{self.identity_url}/invoke"

    @property
    def status_url(self) -> str:
        """Get the full URL to this service's status endpoint."""
        return f"{self.identity_url}/status"

    @property
    def auth_server_url(self) -> str:
        """Get the authorization server URL (default: zone URL or custom)."""
        if self.authorization_server_url:
            return self.authorization_server_url
        return f"https://{self.zone_id}.keycard.cloud"

    def to_agent_card(self) -> dict[str, Any]:
        """Generate agent card metadata for discovery.

        Returns A2A Protocol-compliant AgentCard as a dictionary.
        Uses the standard A2A AgentCard format with Keycard-specific extensions.

        Returns:
            Dictionary representing the agent card in A2A standard format.

        Reference:
            https://a2a-protocol.org/latest/protocol/agent_card/
        """
        # Convert our simple capabilities list to A2A skills
        skills = [
            AgentSkill(
                id=capability,
                name=capability.replace("_", " ").title(),
                description=f"{capability} capability",
                tags=[capability],
            )
            for capability in self.capabilities
        ]

        # Build A2A-compliant agent card
        agent_card = AgentCard(
            name=self.service_name,
            description=self.description or f"{self.service_name} agent service",
            url=self.identity_url,
            version="1.0.0",
            skills=skills,
            capabilities=AgentCapabilities(
                streaming=False,  # We don't support streaming yet
                multi_turn=True,  # We support conversational context
                async_tasks=False,  # Currently synchronous
            ),
            default_input_modes=["text"],
            default_output_modes=["text"],
            preferred_transport="jsonrpc",  # TransportProtocol enum value
            protocol_version="0.3.0",
            # Additional interfaces for our custom invoke endpoint
            additional_interfaces=[
                AgentInterface(
                    url=f"{self.identity_url}/invoke",
                    transport="http+json",  # TransportProtocol enum value
                    description="Keycard-specific invoke endpoint with delegation support",
                )
            ],
            # OAuth security scheme
            security_schemes={
                "oauth2": SecurityScheme(
                    type="oauth2",
                    flows={
                        "authorizationCode": {
                            "authorizationUrl": f"{self.auth_server_url}/oauth/authorize",
                            "tokenUrl": f"{self.auth_server_url}/oauth/token",
                            "scopes": {},
                        }
                    },
                )
            },
            security=[{"oauth2": []}],  # Require OAuth2 authentication
        )

        # Return as dictionary for backward compatibility
        return agent_card.model_dump(mode="json", exclude_none=True)
