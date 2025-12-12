# KeycardAI Agents

Agent service framework for deploying CrewAI and other agent frameworks with Keycard authentication and service-to-service delegation.

## Overview

`keycardai-agents` enables you to deploy AI agent crews as HTTP services with:
- **Service Identity**: Each crew gets a Keycard Application identity
- **Service Discovery**: Agent cards expose capabilities via `/.well-known/agent-card.json`
- **Service-to-Service Delegation**: Agents can delegate tasks to other agent services
- **OAuth Security**: Full RFC 8693 token exchange with delegation chains
- **MCP Tool Integration**: Agents use Phase 1 tool-level authentication for API access

## Installation

```bash
pip install keycardai-agents[crewai]
```

## Quick Start

### Deploy a CrewAI Service

```python
from keycardai.agents import AgentServiceConfig, serve_agent
from crewai import Agent, Crew, Task
import os

def create_my_crew():
    """Factory function to create your crew."""
    agent = Agent(
        role="Analyst",
        goal="Analyze data",
        tools=[...]  # MCP tools + A2A delegation tools
    )

    return Crew(agents=[agent], tasks=[...])

# Configure service
config = AgentServiceConfig(
    service_name="My Analysis Service",
    client_id="analysis_service",
    client_secret=os.getenv("KEYCARD_CLIENT_SECRET"),
    identity_url="https://analysis.example.com",
    zone_id=os.getenv("KEYCARD_ZONE_ID"),
    description="Analyzes data and generates reports",
    capabilities=["data_analysis", "reporting"],
    crew_factory=create_my_crew
)

# Start service (blocking)
serve_agent(config)
```

### Call Another Service (A2A Delegation)

```python
from keycardai.agents import A2AServiceClient
from keycardai.mcp.client.integrations.crewai_agents import create_client

# Get A2A delegation tools
async with create_client(mcp_client, service_config) as client:
    mcp_tools = await client.get_tools()  # GitHub, Slack, etc.
    a2a_tools = await client.get_a2a_tools()  # Other agent services

    # Agent automatically discovers delegation tools
    orchestrator = Agent(
        role="Orchestrator",
        tools=mcp_tools + a2a_tools,
        backstory="Coordinate with other services when needed"
    )
```

## Framework Support

`keycardai-agents` provides agent service orchestration with A2A delegation.

### CrewAI Integration (Full Support)

The agent card server is designed for **CrewAI** workflows:

```python
from keycardai.agents import AgentServiceConfig, serve_agent
from crewai import Agent, Crew, Task

def create_crew():
    agent = Agent(role="Analyst", goal="Analyze data", tools=[...])
    return Crew(agents=[agent], tasks=[...])

config = AgentServiceConfig(
    service_name="My Service",
    crew_factory=create_crew,  # Returns CrewAI Crew instance
    client_id=os.getenv("KEYCARD_CLIENT_ID"),
    client_secret=os.getenv("KEYCARD_CLIENT_SECRET"),
    identity_url=os.getenv("SERVICE_URL"),
    zone_id=os.getenv("KEYCARD_ZONE_ID"),
)

serve_agent(config)  # Deploys crew as HTTP service
```

**Installation:**
```bash
pip install keycardai-agents[crewai]
```

**Features for CrewAI:**
- ✅ Deploy crews as HTTP services with OAuth authentication
- ✅ Automatic A2A tool generation via `get_a2a_tools()`
- ✅ Agent card discovery at `/.well-known/agent-card.json`
- ✅ Service-to-service delegation with token exchange
- ✅ Full delegation chain tracking

**Automatic A2A Tools:**
```python
from keycardai.agents.integrations.crewai_a2a import get_a2a_tools

# Get delegation tools for other services
a2a_tools = await get_a2a_tools(service_config, delegatable_services=[
    {
        "name": "Deployment Service",
        "url": "https://deployer.example.com",
        "description": "Deploys applications to production",
        "capabilities": ["deploy", "test", "rollback"]
    }
])

# Use tools in crew - agent can delegate to other services
agent = Agent(role="Orchestrator", tools=a2a_tools)
```

### Other Frameworks (A2A Client)

**LangChain, LangGraph, AutoGen, Custom Agents:** Use the A2A client to interact with CrewAI services

```bash
pip install keycardai-agents  # No [crewai] needed
```

```python
from keycardai.agents import A2AServiceClient, AgentServiceConfig

# Configure your service identity
config = AgentServiceConfig(
    service_name="My LangChain Service",
    client_id=os.getenv("KEYCARD_CLIENT_ID"),
    client_secret=os.getenv("KEYCARD_CLIENT_SECRET"),
    identity_url="https://my-service.example.com",
    zone_id=os.getenv("KEYCARD_ZONE_ID"),
)

# Create A2A client
client = A2AServiceClient(config)

# Discover CrewAI services
card = await client.discover_service("https://crew-service.com")

# Delegate tasks to CrewAI services
result = await client.invoke_service(
    "https://crew-service.com",
    {"task": "Analyze PR #123", "repo": "org/repo"}
)
```

**What you get:**
- ✅ Call CrewAI services from any framework
- ✅ Service discovery via agent cards
- ✅ OAuth token exchange and authentication
- ✅ Delegation chain tracking
- ⚠️ No server deployment (A2A client only)

**Use Case Example:**
```
Your LangChain/AutoGen Agent
  → A2AServiceClient.invoke_service()
    → CrewAI PR Analysis Service (deployed with keycardai-agents)
      → GitHub MCP tools
      → Returns analysis result
```

### Custom Agents as Services (Advanced)

To deploy **non-CrewAI agents** as HTTP services, implement the `.kickoff(inputs)` interface:

```python
class LangChainServiceWrapper:
    """Wrapper to make LangChain compatible with agent card server."""

    def __init__(self, chain):
        self.chain = chain

    def kickoff(self, inputs: dict) -> str:
        """Adapt LangChain .invoke() to CrewAI .kickoff() interface."""
        # Extract task from inputs
        result = self.chain.invoke(inputs)
        return str(result)

# Deploy wrapped agent as service
from keycardai.agents import AgentServiceConfig, serve_agent

config = AgentServiceConfig(
    service_name="My LangChain Service",
    crew_factory=lambda: LangChainServiceWrapper(my_langchain_chain),
    # ... other config
)

serve_agent(config)  # Now your LangChain agent is an HTTP service
```

**Note:** The agent card server expects a `.kickoff(inputs)` method. Other frameworks need an adapter wrapper.

## Features

### Service Identity
Each deployed crew service has a Keycard Application identity with:
- Client ID and secret for authentication
- Identity URL (e.g., `https://service.example.com`)
- Service-level token for API access

### Agent Card Discovery
Services expose capabilities at `/.well-known/agent-card.json`:
```json
{
  "name": "PR Analysis Service",
  "description": "Analyzes GitHub pull requests",
  "capabilities": ["pr_analysis", "code_review"],
  "endpoints": {
    "invoke": "https://pr-analyzer.example.com/invoke"
  }
}
```

### Service-to-Service Delegation
Agents can delegate tasks to other services:
```python
# Automatic tool generation from Keycard dependencies
tools = await client.get_a2a_tools()  # Returns delegation tools

# Tools like: delegate_to_slack_poster, delegate_to_deployment_service
# Agent uses tools naturally based on LLM decisions
```

### OAuth Token Flow
```
User → Service A (Application identity)
  ├─ Uses MCP tool → API (per-call token exchange)
  └─ Delegates to Service B (service-to-service token exchange)
      └─ Uses MCP tool → API (per-call token exchange)
```

Full delegation chain in audit logs: `User → Service A → Service B → API`

## Relationship to MCP Package

`keycardai-agents` depends on `keycardai-mcp` for shared OAuth infrastructure, but serves a different purpose:

| Package | Purpose | Use Case |
|---------|---------|----------|
| **keycardai-mcp** | Tool-level authentication | Agents calling external APIs (GitHub, Slack) |
| **keycardai-agents** | Service-level orchestration | Agents delegating to other agent services |

### When to Use What

**MCP Tools**: Agent needs to call GitHub API, Slack API, or other external resources
```python
# Example: Fetch PR from GitHub
fetch_pr_tool  # MCP tool that calls GitHub API with delegated token
```

**A2A Delegation**: Agent needs to delegate a complex task to another specialized agent service
```python
# Example: PR Analyzer → Deployment Service → Slack Notifier
delegate_to_deployment_service  # A2A tool that calls another agent
```

**Both Together**: An agent can use MCP tools for external APIs AND delegate to other agents via A2A
```python
# Orchestrator agent with both types of tools
agent = Agent(
    role="Orchestrator",
    tools=[
        *mcp_tools,  # GitHub, Slack API tools
        *a2a_tools   # Delegation to other agent services
    ]
)
```

### Agent Cards vs MCP Metadata

- **Agent Cards** (this package): Service discovery for A2A delegation
  - Endpoint: `/.well-known/agent-card.json`
  - Purpose: Discover agent service capabilities for delegation
  - Custom format specific to Keycard agent services

- **MCP Metadata** (mcp package): OAuth metadata for MCP server authentication
  - Endpoint: `/.well-known/oauth-protected-resource`
  - Purpose: OAuth 2.0 server configuration for MCP protocol
  - Standard OAuth 2.0 RFC 8707 format

- **MCP Protocol** (Anthropic specification): Model Context Protocol
  - Separate specification for AI tool servers
  - Our agent cards are NOT MCP protocol agent cards
  - Different use case: tool servers vs agent orchestration

### Architecture Comparison

```
MCP Flow (Tool-Level):
User → Agent → MCP Tool → MCP Server → External API (GitHub/Slack)
                 ↑
          Per-call token exchange

A2A Flow (Service-Level):
User → Agent Service A → Agent Service B → External APIs
            ↑                    ↑
      Service identity     Service identity
      Service-to-service token exchange
```

**Key Insight**: MCP and A2A are complementary, not competing. Use MCP for external API access, A2A for agent orchestration.

## Architecture

### Keycard Configuration

```yaml
# Applications (Service Identities)
applications:
  - client_id: pr_analyzer_service
    identity_url: https://pr-analyzer.example.com
  - client_id: slack_poster_service
    identity_url: https://slack-poster.example.com

# Resources (Protected Endpoints)
resources:
  - id: slack_poster_api
    url: https://slack-poster.example.com
    type: agent_service
  - id: github_mcp_server
    url: https://github-mcp.example.com
    type: mcp_server

# Dependencies (Access Control)
dependencies:
  - application: pr_analyzer_service
    resource: github_mcp_server
    permissions: [read]
  - application: pr_analyzer_service
    resource: slack_poster_api
    permissions: [invoke]
```

## Production Deployment

### Security Requirements

#### Token Validation
The agent card server validates OAuth bearer tokens using JWKS-based signature verification. In production:

1. **JWKS Validation**: Tokens are validated against Keycard's public keys from `/.well-known/jwks.json`
2. **Signature Verification**: JWT signatures are verified using RSA public key cryptography
3. **Audience Check**: Token audience (`aud`) must match service identity URL
4. **Issuer Validation**: Token issuer (`iss`) must match Keycard zone
5. **Expiration Check**: Expired tokens are rejected
6. **Delegation Chain**: Preserved through multi-hop delegation for audit trails

#### Configuration Best Practices
```python
import os

config = AgentServiceConfig(
    service_name="Production Service",
    client_id=os.getenv("KEYCARD_CLIENT_ID"),  # NEVER hardcode
    client_secret=os.getenv("KEYCARD_CLIENT_SECRET"),  # NEVER hardcode
    identity_url=os.getenv("SERVICE_URL"),  # From environment
    zone_id=os.getenv("KEYCARD_ZONE_ID"),
    # Optional but recommended
    description="Production service description",
    capabilities=["capability1", "capability2"],
)

# ✅ DO: Use environment variables
# ❌ DON'T: Hardcode credentials in source code
```

### Error Handling

Services should handle these error scenarios:

| Error | Status Code | Cause | Action |
|-------|-------------|-------|--------|
| Missing Authorization | 401 | No `Authorization` header | Add `Bearer <token>` header |
| Invalid Token | 401 | JWT signature invalid or expired | Get new token from Keycard |
| Audience Mismatch | 403 | Token not scoped to this service | Request token with correct `resource` parameter |
| No Crew Factory | 501 | Service configured without crew | Add `crew_factory` to config |
| Crew Execution Failed | 500 | Exception during crew execution | Check crew logs, fix crew logic |
| Service Unavailable | 503 | Service overloaded or down | Retry with exponential backoff |

### Monitoring

Key metrics to track for production agent services:

**Token Operations:**
- Token exchange success/failure rate
- Token validation latency
- JWKS fetch performance
- Token expiration events

**Service Performance:**
- Crew execution latency (p50, p95, p99)
- Delegation chain depth distribution
- Service invocation rate
- Error rate by type (401, 403, 500, 503)

**Cache Performance:**
- Agent card cache hit/miss ratio
- Cache expiration events
- Cache size and memory usage

**Audit Trail:**
```python
# Logs automatically include:
logger.info(f"Invoke request from user={user_id}, service={client_id}, chain={delegation_chain}")
logger.info(f"Obtained delegation token for {target_service} (expires_in={expires_in})")
logger.info(f"Service invocation successful: {target_service}")
```

### Deployment Patterns

**Single Service** (Simple):
```bash
# Deploy agent service
python -m my_service
# Exposes:
# - GET /.well-known/agent-card.json (public)
# - POST /invoke (protected)
# - GET /status (public)
```

**Multi-Service** (Microservices):
```
User
 ├─ PR Analysis Service (https://pr-analyzer.example.com)
 │   ├─ Uses: GitHub MCP tools
 │   └─ Delegates to: Deployment Service
 └─ Deployment Service (https://deployer.example.com)
     ├─ Uses: CI/CD MCP tools
     └─ Delegates to: Slack Notification Service
```

**Load Balancing**:
- Multiple instances of same service behind load balancer
- All instances share same `client_id` and `identity_url`
- Stateless design enables horizontal scaling

### Environment Variables

Required for production:
```bash
# Keycard Authentication
export KEYCARD_ZONE_ID="your_zone_id"
export KEYCARD_CLIENT_ID="your_service_client_id"
export KEYCARD_CLIENT_SECRET="your_client_secret"

# Service Identity
export SERVICE_URL="https://your-service.example.com"
export PORT="8000"
export HOST="0.0.0.0"

# Optional: MCP Server URLs (if using MCP tools)
export GITHUB_MCP_SERVER_URL="https://github-mcp.example.com"
export SLACK_MCP_SERVER_URL="https://slack-mcp.example.com"

# Optional: Delegatable Services (if not using Keycard discovery)
export DEPLOYMENT_SERVICE_URL="https://deployer.example.com"
```

### Health Checks

```bash
# Liveness probe
curl https://your-service.example.com/status
# Expected: {"status": "healthy", "service": "...", "identity": "...", "version": "..."}

# Agent card discovery
curl https://your-service.example.com/.well-known/agent-card.json
# Expected: Agent card JSON with capabilities
```

### JWKS Caching

The agent card server fetches JWKS from Keycard for token validation. To optimize:
- JWKS keys are fetched per-request (no built-in caching yet)
- Consider adding a caching layer (Redis, in-memory) for JWKS
- JWKS typically updates infrequently (hours/days)

### Logging Configuration

```python
import logging

# Production logging setup
logging.basicConfig(
    level=logging.INFO,  # Use INFO in production (not DEBUG)
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Adjust specific loggers
logging.getLogger("keycardai.agents").setLevel(logging.INFO)
logging.getLogger("keycardai.oauth").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.INFO)
```

## API Reference

### AgentServiceConfig

Configuration for deploying an agent service.

**Parameters:**
- `service_name` (str): Human-readable service name
- `client_id` (str): Keycard Application client ID
- `client_secret` (str): Keycard Application client secret
- `identity_url` (str): Public URL of this service
- `zone_id` (str): Keycard zone identifier
- `port` (int): HTTP server port (default: 8000)
- `host` (str): Bind address (default: "0.0.0.0")
- `description` (str): Service description for agent card
- `capabilities` (list[str]): List of capabilities for discovery
- `crew_factory` (Callable): Function that returns a Crew instance

### serve_agent()

Start an agent service (blocking call).

**Parameters:**
- `config` (AgentServiceConfig): Service configuration

**Returns:** None (blocks until shutdown)

### A2AServiceClient

Client for service-to-service delegation.

**Methods:**
- `discover_service(service_url)`: Fetch agent card from service
- `get_delegation_token(target_url)`: Get OAuth token for service
- `invoke_service(url, task, token)`: Call another agent service

**Available Variants:**
- `A2AServiceClient`: Async version for async workflows
- `A2AServiceClientSync`: Synchronous version for sync workflows (e.g., CrewAI)

### ServiceDiscovery

Agent card discovery with caching.

**Parameters:**
- `service_config` (AgentServiceConfig): Service configuration
- `cache_ttl` (int): Cache TTL in seconds (default: 900 = 15 minutes)

**Methods:**
- `get_service_card(service_url, force_refresh)`: Get agent card with caching
- `list_delegatable_services()`: List services from Keycard dependencies (placeholder)
- `clear_cache()`: Clear all cached agent cards
- `clear_service_cache(service_url)`: Clear specific service cache
- `get_cache_stats()`: Get cache statistics

**Usage:**
```python
async with ServiceDiscovery(service_config, cache_ttl=600) as discovery:
    card = await discovery.get_service_card("https://service.example.com")
    stats = discovery.get_cache_stats()
```

### get_a2a_tools()

Generate CrewAI tools for A2A delegation (CrewAI integration).

**Parameters:**
- `service_config` (AgentServiceConfig): Service configuration
- `delegatable_services` (list[dict] | None): List of services or None to discover

**Returns:** List of CrewAI `BaseTool` objects for delegation

**Example:**
```python
from keycardai.agents.integrations.crewai_a2a import get_a2a_tools

tools = await get_a2a_tools(service_config, delegatable_services=[
    {"name": "Service", "url": "...", "description": "...", "capabilities": [...]}
])
```

## Examples

See `/examples` directory for complete working examples:
- `pr_analysis_service/` - Analyzes PRs and delegates to Slack
- `slack_notification_service/` - Receives tasks and posts to Slack

## License

MIT
