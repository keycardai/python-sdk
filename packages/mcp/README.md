# KeyCard AI MCP SDK

A comprehensive Python SDK for Model Context Protocol (MCP) functionality that simplifies authentication and authorization concerns for developers working with AI/LLM integrations.

## Installation

```bash
pip install keycardai-mcp
```

## Quick Start

```python
from keycardai.mcp import *

# MCP Server with authentication
server = MCPServer(
    name="my-mcp-server",
    version="1.0.0",
    auth_config=MCPAuthConfig(
        oauth_client_id="your_client_id",
        oauth_client_secret="your_client_secret"
    )
)

# Register authenticated resources
@server.resource("user-data")
async def get_user_data(context: MCPContext) -> MCPResource:
    # Automatic token validation and user context
    user = context.authenticated_user
    return MCPResource(
        uri=f"user://{user.id}/data",
        content=await fetch_user_data(user.id)
    )

# MCP Client with token management
client = MCPClient(
    server_url="https://api.example.com/mcp",
    auth=MCPOAuthAuth(
        client_id="client_id",
        client_secret="client_secret"
    )
)

# Access authenticated resources
user_data = await client.get_resource("user-data")
```

## 🏗️ Architecture & Features

This SDK provides comprehensive MCP functionality with enterprise-grade security:

### Core MCP Components

| Component | Module | Description |
|-----------|---------|-------------|
| **MCP Server** | `server.py` | **Authenticated MCP Server** - Host MCP resources with built-in OAuth 2.0 authentication |
| **MCP Client** | `client.py` | **Secure MCP Client** - Connect to MCP servers with automatic token management |
| **Resource Management** | `resources.py` | **Authenticated Resources** - Secure resource access with user context |
| **Tool Integration** | `tools.py` | **Secure Tools** - Execute MCP tools with proper authorization |

### Authentication & Security

| Feature | Module | Description |
|---------|---------|-------------|
| **OAuth 2.0 Integration** | `auth.py` | **Token Management** - Seamless OAuth integration for MCP operations |
| **Token Validation** | `validation.py` | **Security Middleware** - Automatic token validation and user context |
| **Scope Management** | `scopes.py` | **Permission Control** - Fine-grained access control for MCP resources |
| **Session Management** | `sessions.py` | **Secure Sessions** - Persistent authenticated sessions for MCP clients |

### MCP Protocol Extensions

| Standard | Module | Description |
|----------|---------|-------------|
| **Resource Templates** | `templates.py` | **Dynamic Resources** - Template-based resource generation with auth context |
| **Prompt Security** | `prompts.py` | **Secure Prompts** - User-aware prompt templates and execution |
| **Tool Authorization** | `tools.py` | **Permission Checks** - Role-based access control for MCP tools |
| **Logging & Monitoring** | `monitoring.py` | **Security Audit** - Comprehensive logging of authenticated operations |

## Features

- ✅ **MCP Protocol Compliance**: Full implementation of Model Context Protocol standards
- ✅ **OAuth 2.0 Integration**: Seamless authentication with industry-standard OAuth flows
- ✅ **Type Safe**: Full type hints with Pydantic models for all MCP operations
- ✅ **Async Support**: Native async/await support for all MCP operations
- ✅ **Enterprise Security**: Token validation, scope management, and audit logging
- ✅ **Developer Friendly**: Simplified API that abstracts away authentication complexity
- ✅ **Production Ready**: Battle-tested security patterns and comprehensive error handling

## Use Cases

### 🤖 AI Agent Platforms
```python
# Secure MCP server for AI agents
server = MCPServer(auth_required=True)

@server.tool("execute-query")
async def execute_query(context: MCPContext, query: str) -> dict:
    # Only authenticated users with 'query:execute' scope
    if not context.has_scope("query:execute"):
        raise MCPAuthError("Insufficient permissions")
    
    return await database.execute(query, user=context.user)
```

### 🔐 Enterprise LLM Integration
```python
# Corporate LLM with secure resource access
client = MCPClient(
    server_url="https://corp-llm.company.com/mcp",
    auth=MCPOAuthAuth.from_client_credentials(
        client_id="corp-client",
        client_secret="secret",
        scopes=["documents:read", "calendar:read"]
    )
)

# Access corporate resources securely
documents = await client.list_resources("documents")
```

### 🌐 Multi-Tenant SaaS
```python
# Tenant-aware MCP resources
@server.resource("tenant-data")
async def get_tenant_data(context: MCPContext) -> MCPResource:
    tenant_id = context.user.tenant_id
    return await fetch_tenant_data(tenant_id, user=context.user)
```

## Security Best Practices

### Token Management
- Automatic token refresh and rotation
- Secure token storage with encryption
- Scope-based permission validation
- Session timeout and cleanup

### Authentication Flows
- Authorization Code flow for web applications
- Client Credentials flow for service-to-service
- Device Code flow for CLI applications
- PKCE for public clients

### Monitoring & Compliance
- Comprehensive audit logging
- Rate limiting and abuse prevention
- GDPR-compliant user data handling
- SOC 2 security controls

## Development

This package is part of the [KeycardAI Python SDK workspace](../../README.md). 

To develop:

```bash
# From workspace root
uv sync
uv run --package keycardai-mcp pytest
```

## Examples

See the [examples directory](examples/) for comprehensive examples including:
- Basic MCP server setup
- OAuth integration patterns
- Multi-tenant configurations
- Enterprise deployment guides

## License

MIT License - see [LICENSE](../../LICENSE) file for details.

## Support

- 📖 [Documentation](https://docs.keycardai.com/mcp)
- 🐛 [Issue Tracker](https://github.com/keycardai/python-sdk/issues)
- 💬 [Community Discussions](https://github.com/keycardai/python-sdk/discussions)
- 📧 [Support Email](mailto:support@keycard.ai)
