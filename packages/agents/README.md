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

## Examples

See `/examples` directory for complete working examples:
- `pr_analysis_service/` - Analyzes PRs and delegates to Slack
- `slack_notification_service/` - Receives tasks and posts to Slack

## License

MIT
