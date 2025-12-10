"""Service configuration for agent services."""

from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class AgentServiceConfig:
    """Configuration for deploying a crew as an HTTP service with Keycard identity.

    This configuration enables an agent crew to be deployed as a standalone HTTP service
    with its own Keycard Application identity, capable of:
    - Serving requests via REST API
    - Exposing capabilities via agent card
    - Delegating to other agent services (A2A)
    - Using MCP tools with per-call authentication

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
        crew_factory: Callable that returns a Crew instance (or None for custom implementations)

    Example:
        >>> from keycardai.agents import AgentServiceConfig
        >>> config = AgentServiceConfig(
        ...     service_name="PR Analysis Service",
        ...     client_id="pr_analyzer_service",
        ...     client_secret="secret_123",
        ...     identity_url="https://pr-analyzer.example.com",
        ...     zone_id="xr9r33ga15",
        ...     description="Analyzes GitHub pull requests",
        ...     capabilities=["pr_analysis", "code_review"],
        ...     crew_factory=lambda: create_pr_crew()
        ... )
    """

    # Service identity (Keycard Application)
    service_name: str
    client_id: str
    client_secret: str
    identity_url: str
    zone_id: str

    # Deployment configuration
    port: int = 8000
    host: str = "0.0.0.0"

    # Agent card metadata
    description: str = ""
    capabilities: list[str] = field(default_factory=list)

    # Crew/agent implementation
    crew_factory: Callable[[], Any] | None = None

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

    def to_agent_card(self) -> dict[str, Any]:
        """Generate agent card metadata for discovery.

        Returns:
            Dictionary representing the agent card in standard format.
        """
        return {
            "name": self.service_name,
            "description": self.description,
            "type": "crew_service",
            "identity": self.identity_url,
            "capabilities": self.capabilities,
            "endpoints": {
                "invoke": self.invoke_url,
                "status": self.status_url,
            },
            "auth": {
                "type": "oauth2",
                "token_url": f"https://{self.zone_id}.keycard.cloud/oauth/token",
                "resource": self.identity_url,
            },
        }
