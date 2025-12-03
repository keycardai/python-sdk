## 0.1.0-keycardai-agents (2025-12-03)

### Features

- feat(keycardai-agents): initial release
- CrewAI integration with secure MCP tool access
- No token passing - agents never receive raw API tokens
- Fresh token fetched per API call through Keycard
- Authentication awareness with auth request tools
- Automatic tool conversion from MCP to CrewAI BaseTool format
- Configurable auth handlers for custom authentication flows
- System prompt generation with auth context
