"""Service configuration for agent services."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a2a.server.agent_execution import AgentExecutor


@dataclass
class AgentServiceConfig:
    """Configuration for deploying an agent service with Keycard identity.

    This configuration enables an agent to be deployed as a standalone HTTP
    service with its own Keycard Application identity, capable of:

    - Serving requests via the standard A2A JSONRPC protocol
    - Exposing capabilities via the agent card (`.well-known/agent-card.json`)
    - Delegating to other agent services using OAuth 2.0 token exchange
    - Verifying inbound bearer tokens against Keycard

    The service uses a2a-sdk's native ``AgentExecutor`` (async, event-driven).
    Customers subclass ``a2a.server.agent_execution.AgentExecutor`` and supply
    an instance via ``agent_executor``.

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
        agent_executor: A subclass of ``a2a.server.agent_execution.AgentExecutor``
            implementing ``async def execute(context, event_queue)`` and
            ``async def cancel(context, event_queue)``.

    Example:
        >>> from a2a.server.agent_execution import AgentExecutor
        >>> from a2a.server.events.event_queue_v2 import EventQueue
        >>> from a2a.server.agent_execution.context import RequestContext
        >>> from keycardai.a2a import AgentServiceConfig
        >>>
        >>> class EchoExecutor(AgentExecutor):
        ...     async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        ...         text = context.get_user_input()
        ...         # publish a Message event to event_queue ...
        ...     async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        ...         pass
        >>>
        >>> config = AgentServiceConfig(
        ...     service_name="PR Analysis Service",
        ...     client_id="pr_analyzer_service",
        ...     client_secret="secret_123",
        ...     identity_url="https://pr-analyzer.example.com",
        ...     zone_id="xr9r33ga15",
        ...     description="Analyzes GitHub pull requests",
        ...     capabilities=["pr_analysis", "code_review"],
        ...     agent_executor=EchoExecutor(),
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
    def jsonrpc_url(self) -> str:
        """Get the full URL to this service's A2A JSONRPC endpoint."""
        return f"{self.identity_url}/a2a/jsonrpc"

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
