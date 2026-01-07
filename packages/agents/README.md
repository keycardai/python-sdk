# KeycardAI Agents

Framework-agnostic agent service SDK for A2A (Agent-to-Agent) delegation with Keycard OAuth authentication.

## Features

- 🔐 **Built-in OAuth**: Automatic JWKS validation, token exchange, delegation chains
- 🌐 **Dual Protocol Support**: A2A JSONRPC + custom REST endpoints (same executor powers both)
- 🔧 **Framework Agnostic**: Supports CrewAI, LangChain, custom via `AgentExecutor` protocol
- 🔄 **Service Delegation**: RFC 8693 token exchange preserves user context
- 👤 **User Auth**: PKCE OAuth flow with browser-based login

## A2A Protocol Integration

We use [a2a-python SDK](https://github.com/a2aproject/a2a-python) for protocol compliance while adding production-ready authentication:

- ✅ **Full A2A JSONRPC support** - Standards-compliant `/a2a/jsonrpc` endpoint
- ✅ **Plus simpler REST endpoint** - Custom `/invoke` for easier integration
- ✅ **Production OAuth layer** - BearerAuthMiddleware, JWKS, token exchange (A2A SDK has none)
- ✅ **Delegation chain tracking** - JWT-based audit trail for service-to-service calls
- ✅ **Dual protocol support** - Same executor powers both JSONRPC and REST endpoints

**Result**: A2A standards compliance + Keycard security + flexible APIs = Best of both worlds

## Installation

```bash
pip install keycardai-agents

# With CrewAI support
pip install 'keycardai-agents[crewai]'
```

## Quick Start

### CrewAI Service

```python
import os
from crewai import Agent, Crew, Task
from keycardai.agents import AgentServiceConfig
from keycardai.agents.integrations.crewai import CrewAIExecutor
from keycardai.agents.server import serve_agent

def create_my_crew():
    agent = Agent(role="Assistant", goal="Help users", backstory="AI helper")
    task = Task(description="{task}", agent=agent, expected_output="Response")
    return Crew(agents=[agent], tasks=[task])

config = AgentServiceConfig(
    service_name="My Service",
    client_id=os.getenv("CLIENT_ID"),
    client_secret=os.getenv("CLIENT_SECRET"),
    identity_url="http://localhost:8000",
    zone_id=os.getenv("ZONE_ID"),
    agent_executor=CrewAIExecutor(create_my_crew),  # Framework adapter
    capabilities=["assistance"],
)

serve_agent(config)  # Starts server with OAuth middleware
```

### Custom Executor

```python
from keycardai.agents.server import LambdaExecutor

def my_logic(task, inputs):
    return f"Processed: {task}"

config = AgentServiceConfig(
    # ... same config as above
    agent_executor=LambdaExecutor(my_logic),  # Simple function wrapper
)
```

### Advanced: Custom Executor Class

```python
from keycardai.agents.server import AgentExecutor

class MyFrameworkExecutor:
    """Implement AgentExecutor protocol for any framework."""

    def execute(self, task, inputs):
        # Your framework logic here
        result = my_framework.run(task, inputs)
        return result

    def set_token_for_delegation(self, access_token):
        # Optional: handle delegation token
        self.context.set_auth(access_token)

config = AgentServiceConfig(
    # ...
    agent_executor=MyFrameworkExecutor(),
)
```

## Client Usage

### User Authentication (PKCE)

```python
from keycardai.agents.client import AgentClient

async with AgentClient(config) as client:
    # Automatically: OAuth discovery → Browser login → Token exchange
    result = await client.invoke("https://service.com", task="Hello")
```

### Service-to-Service (Token Exchange)

```python
from keycardai.agents.server import DelegationClient

client = DelegationClient(service_config)

# Get delegation token (RFC 8693) - preserves user context
token = await client.get_delegation_token(
    "https://target.com",
    subject_token="user_token"
)

# Invoke with token
result = await client.invoke_service(
    "https://target.com",
    task="Process data",
    token=token
)
# Result includes delegation_chain: ["service_a", "service_b"]
```

## Architecture

### Server

```
Your Agent
  ↓
AgentExecutor.execute(task, inputs)
  ↓
AgentServer (keycardai-agents)
  ├─ OAuth Middleware (BearerAuthMiddleware)
  │  ├─ JWKS validation
  │  ├─ Token audience check
  │  └─ Delegation chain extraction
  ├─ /invoke (protected, REST-like)
  ├─ /a2a/jsonrpc (protected, A2A JSONRPC)
  │  ├─ message/send
  │  ├─ message/stream
  │  └─ tasks/* (get, cancel, list)
  ├─ /.well-known/agent-card.json (A2A format)
  ├─ /.well-known/oauth-protected-resource
  └─ /status
```

### Dual Protocol Support

The SDK provides **two ways** to invoke agents:

1. **A2A JSONRPC** (`/a2a/jsonrpc`) - Standards-compliant
   - Use when: Integrating with A2A ecosystem, need standard protocol
   - Methods: `message/send`, `message/stream`, `tasks/get`, etc.
   - Bridge: `KeycardToA2AExecutorBridge` adapts your executor to A2A protocol

2. **Custom REST** (`/invoke`) - Simpler API
   - Use when: Direct service calls, simpler integration
   - Format: `{"task": "...", "inputs": {...}}`
   - Direct executor invocation

**Both endpoints share the same underlying executor** - write once, support both protocols.

### OAuth Flow

```
User → OAuth Login (PKCE)
  ↓
User Token → Service A
  ↓
Service A → Token Exchange (RFC 8693) → Service B Token
  ↓
Service A → Calls Service B with Service B Token
  ↓
Service B validates token (JWKS)
Service B updates delegation_chain
```

## A2A Protocol Compliance

### Agent Card

Services expose A2A-compliant agent cards at `/.well-known/agent-card.json`:

```json
{
  "name": "My Service",
  "url": "https://my-service.com",
  "version": "1.0.0",
  "protocolVersion": "0.3.0",
  "skills": [
    {
      "id": "assistance",
      "name": "Assistance",
      "description": "assistance capability",
      "tags": ["assistance"]
    }
  ],
  "capabilities": {
    "streaming": false,
    "multiTurn": true
  },
  "additionalInterfaces": [
    {
      "url": "https://my-service.com/invoke",
      "transport": "http+json"
    }
  ],
  "securitySchemes": {
    "oauth2": {
      "type": "oauth2",
      "flows": {
        "authorizationCode": {
          "authorizationUrl": "https://zone.keycard.cloud/oauth/authorize",
          "tokenUrl": "https://zone.keycard.cloud/oauth/token"
        }
      }
    }
  }
}
```

### Endpoints

#### A2A JSONRPC Endpoint (Standards-Compliant)

```bash
POST /a2a/jsonrpc
Authorization: Bearer <token>
Content-Type: application/json

{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [{"text": "Do something"}]
    }
  },
  "id": 1
}
```

Response:
```json
{
  "jsonrpc": "2.0",
  "result": {
    "task": {
      "taskId": "task-123",
      "state": "completed",
      "result": {...}
    }
  },
  "id": 1
}
```

**Supported methods:**
- `message/send` - Send message to agent
- `message/stream` - Stream agent responses
- `tasks/get` - Get task status
- `tasks/cancel` - Cancel running task
- `tasks/list` - List all tasks

#### Custom REST Endpoint (Simpler API)

```bash
POST /invoke
Authorization: Bearer <token>

{
  "task": "Do something",
  "inputs": {"key": "value"}
}
```

Response:
```json
{
  "result": "Done",
  "delegation_chain": ["service_a", "service_b"]
}
```

**Use `/invoke` for:** Direct service calls, easier integration, delegation chain tracking.

**Use `/a2a/jsonrpc` for:** A2A ecosystem integration, standard protocol compliance, task management.

## Framework Support

### CrewAI

```python
from keycardai.agents.integrations.crewai import CrewAIExecutor

executor = CrewAIExecutor(lambda: create_my_crew())
```

**Features:**
- Automatic delegation token context
- Supports CrewAI tools
- Handles `crew.kickoff()` execution

### LangChain, AutoGen, Custom

Implement the `AgentExecutor` protocol:

```python
class MyExecutor:
    def execute(self, task, inputs):
        # Your logic
        return result
```

## API Reference

### AgentServiceConfig

```python
@dataclass
class AgentServiceConfig:
    service_name: str              # Human-readable name
    client_id: str                 # Keycard Application client ID
    client_secret: str             # Keycard Application secret
    identity_url: str              # Public URL
    zone_id: str                   # Keycard zone ID
    agent_executor: AgentExecutor  # REQUIRED: Executor instance

    # Optional
    authorization_server_url: str | None = None
    port: int = 8000
    host: str = "0.0.0.0"
    description: str = ""
    capabilities: list[str] = []
```

### AgentExecutor Protocol

```python
class AgentExecutor(Protocol):
    def execute(
        self,
        task: dict[str, Any] | str,
        inputs: dict[str, Any] | None = None,
    ) -> Any:
        """Execute agent task."""
        ...

    def set_token_for_delegation(self, access_token: str) -> None:
        """Optional: Set token for delegation."""
        ...
```

### KeycardToA2AExecutorBridge

Bridge adapter that makes your executor work with A2A JSONRPC protocol:

```python
from keycardai.agents.server import KeycardToA2AExecutorBridge, SimpleExecutor

# Your executor
executor = SimpleExecutor()

# Wrap for A2A JSONRPC support
a2a_executor = KeycardToA2AExecutorBridge(executor)

# Now works with A2A DefaultRequestHandler
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

handler = DefaultRequestHandler(
    agent_executor=a2a_executor,
    task_store=InMemoryTaskStore()
)
```

**What it does:**
- Converts A2A `RequestContext` → Keycard `task/inputs` format
- Calls your synchronous executor
- Publishes result as A2A Task events
- Handles delegation tokens

**Note:** This bridge is automatically configured when using `serve_agent()` - you don't need to use it directly unless building custom A2A integrations.

### serve_agent()

Start an agent service (blocking):

```python
serve_agent(config: AgentServiceConfig) -> None
```

### AgentClient

User authentication with PKCE OAuth:

```python
from keycardai.agents.client import AgentClient

async with AgentClient(service_config) as client:
    result = await client.invoke(service_url, task, inputs)
    agent_card = await client.discover_service(service_url)
```

### DelegationClient

Service-to-service with token exchange:

```python
from keycardai.agents.server import DelegationClient

client = DelegationClient(service_config)
token = await client.get_delegation_token(target_url, subject_token)
result = await client.invoke_service(url, task, token)
```

## Service Delegation

### Pattern

```python
# In Service A (orchestrator)
from keycardai.agents.server import DelegationClient

client = DelegationClient(service_a_config)

# Discover Service B
card = await client.discover_service("https://service-b.com")

# Get token with user context
token = await client.get_delegation_token(
    "https://service-b.com",
    subject_token=user_access_token
)

# Call Service B
result = await client.invoke_service(
    "https://service-b.com",
    task="Process data",
    token=token
)

# Result includes delegation chain for audit
print(result["delegation_chain"])
# ["user_service", "service_a", "service_b"]
```

### Delegation Chain Tracking

1. User authenticates → Token with empty `delegation_chain`
2. User calls Service A → Service A adds itself to chain
3. Service A calls Service B → Token exchange preserves chain
4. Service B adds itself → Full chain in response for audit

## Production Deployment

### Environment Variables

```bash
# Required
export KEYCARD_ZONE_ID="your_zone_id"
export KEYCARD_CLIENT_ID="service_client_id"
export KEYCARD_CLIENT_SECRET="client_secret"
export SERVICE_URL="https://your-service.com"

# Optional
export PORT="8000"
export HOST="0.0.0.0"
```

### Health Checks

```bash
# Liveness
curl https://your-service.com/status

# Agent card
curl https://your-service.com/.well-known/agent-card.json
```

### Security

- **Token Validation**: JWKS-based JWT signature verification
- **Audience Check**: Token `aud` must match service URL
- **Issuer Validation**: Token `iss` from Keycard zone
- **Delegation Chain**: Preserved for audit trail

## Examples

See `examples/` directory:
- `oauth_client_usage.py` - PKCE user authentication

## FAQ

### Q: Why not use the A2A SDK server?
**A**: The A2A SDK has no authentication layer. We'd have to rebuild all OAuth infrastructure.

### Q: Can I use LangChain/AutoGen?
**A**: Yes! Implement the `AgentExecutor` protocol or use `LambdaExecutor` for simple functions.

### Q: What's the difference between AgentClient and DelegationClient?
**A**:
- `AgentClient`: User authentication with PKCE (browser-based login)
- `DelegationClient`: Service-to-service with token exchange (RFC 8693)

### Q: Do I need CrewAI?
**A**: No! Use any framework or write custom logic. Just implement `AgentExecutor`.

## Support

- **GitHub**: https://github.com/keycardai/python-sdk
- **Issues**: https://github.com/keycardai/python-sdk/issues
- **Docs**: https://docs.keycard.ai

## License

MIT
