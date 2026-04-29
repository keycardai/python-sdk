"""Service configuration for Keycard-protected A2A agent services."""

from dataclasses import dataclass, field


@dataclass
class AgentServiceConfig:
    """Identity, credentials, and agent-card metadata for a Keycard-protected
    agent service.

    The shape is intentionally a config bag, not a server abstraction.
    Customers compose this with a2a-sdk's primitives in their own server
    setup. The fields here are the ones consumed by ``DelegationClient``,
    ``ServiceDiscovery``, and ``build_agent_card_from_config``.

    Args:
        service_name: Human-readable name of the service.
        client_id: Keycard Application client ID (service identity).
        client_secret: Keycard Application client secret.
        identity_url: Public URL where this service is reachable.
        zone_id: Keycard zone identifier.
        authorization_server_url: Optional override of the default
            ``https://{zone_id}.keycard.cloud`` authorization server URL.
        description: Free text description; surfaced in the agent card.
        capabilities: Capability tags; one ``AgentSkill`` is generated per tag.

    Example:
        >>> from keycardai.a2a import AgentServiceConfig
        >>>
        >>> config = AgentServiceConfig(
        ...     service_name="PR Analysis Service",
        ...     client_id="pr_analyzer_service",
        ...     client_secret="secret_123",
        ...     identity_url="https://pr-analyzer.example.com",
        ...     zone_id="xr9r33ga15",
        ...     description="Analyzes GitHub pull requests",
        ...     capabilities=["pr_analysis", "code_review"],
        ... )
    """

    # Service identity (Keycard Application)
    service_name: str
    client_id: str
    client_secret: str
    identity_url: str
    zone_id: str

    # Optional configuration
    authorization_server_url: str | None = None

    # Agent card metadata
    description: str = ""
    capabilities: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if self.identity_url.endswith("/"):
            self.identity_url = self.identity_url.rstrip("/")

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

        if not self.identity_url.startswith(("http://", "https://")):
            raise ValueError("identity_url must start with http:// or https://")

    @property
    def agent_card_url(self) -> str:
        """Get the full URL to this service's agent card."""
        return f"{self.identity_url}/.well-known/agent-card.json"

    @property
    def jsonrpc_url(self) -> str:
        """Get the full URL to this service's A2A JSONRPC endpoint."""
        return f"{self.identity_url}/a2a/jsonrpc"

    @property
    def auth_server_url(self) -> str:
        """Get the authorization server URL (default: zone URL or custom)."""
        if self.authorization_server_url:
            return self.authorization_server_url
        return f"https://{self.zone_id}.keycard.cloud"
