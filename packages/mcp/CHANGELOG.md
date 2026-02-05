## 0.20.0-keycardai-mcp (2026-01-07)


- feat(keycardai-mcp): Adds PydanticAI integration for MCP frameworks
- - Adds PaydanticAI adapter to client integrations directory
- Support for PydanticAI agents with secure MCP tool access
- Follows established pattern with LangChain and OpenAI integrations
- Adds tests for PydanticAI integration imports

## 0.19.0-keycardai-mcp (2026-01-07)


- feat(keycardai-mcp): Add greater control over OAuth metadata location
- - Refactors `auth_metadata_mount` into it's component parts
- Exposes mounts for individual metadata
- Allows the user to specify exactly where their OAuth metadata is
exposed
- NOTE: This is only for advanced use cases where you know you need
something non-standard. Otherwise, follow the OAuth spec.

## 0.18.0-keycardai-mcp (2025-12-04)


- feat(keycardai-mcp): add CrewAI integration for agent frameworks
- - Add CrewAI adapter to client integrations directory
- Support for CrewAI agents with secure MCP tool access
- No token passing - agents never receive raw API tokens
- Fresh token fetched per API call through Keycard
- Follows established pattern with LangChain and OpenAI integrations
- Deleted separate packages/agents package (not needed)
- Added optional dependencies: crewai and agents extras
- Added tests for CrewAI integration imports

## 0.17.0-keycardai-mcp (2025-11-18)


- feat(keycardai-mcp): session callback notification
- feat(keycardai-mcp): session lifecycle management

## 0.16.0-keycardai-mcp (2025-11-17)


- feat(keycardai-mcp): headless clients
- feat(keycardai-mcp): update oauth deps
- feat(keycardai-mcp): client implementation

## 0.15.0-keycardai-mcp (2025-11-07)


- feat(keycardai-mcp): enable web token eks env

## 0.14.0-keycardai-mcp (2025-11-06)


- feat(keycardai-mcp): configure mcp url via env

## 0.13.0-keycardai-mcp (2025-11-05)


- feat(keycardai-mcp): zone settings via env

## 0.12.0-keycardai-mcp (2025-11-05)


- feat(keycardai-mcp): automatic app cred discovery
- feat(keycardai-mcp): default eks env

## 0.11.0-keycardai-mcp (2025-10-29)


- feat(keycardai-mcp): release latest version
- Release current version of workload identity implementation

## 0.10.0-keycardai-mcp (2025-10-27)


- feat(keycardai-mcp): cach the application credentials
- feat(keycardai-mcp): app credential grant flow

## 0.9.0-keycardai-mcp (2025-10-20)


- refactor(keycardai-mcp): align credential names
- feat(keycardai-mcp): eks workload identity support
- feat(keycardai-mcp): add application authentication

## 0.8.1-keycardai-mcp (2025-10-10)


- fix(keycardai-mcp): wrong base url in auth metadata

## 0.8.0-keycardai-mcp (2025-10-07)


- refactor(keycardai-mcp): improve error messages
- refactor(keycardai-mcp): improves the error messages to provide useful debug information

## 0.7.1-keycardai-mcp (2025-09-29)


- fix(keycardai-mcp): set audience for client assertions

## 0.7.0-keycardai-mcp (2025-09-27)


- feat(keycardai-mcp): lowlevel support for RequestContext

## 0.6.0-keycardai-mcp (2025-09-23)


- feat(keycardai-mcp): enable custom middleware injection

## 0.5.1-keycardai-mcp (2025-09-22)


- fix(keycardai-mcp): support x-forwarded-port header

## 0.5.0-keycardai-mcp (2025-09-22)


- feat(keycardai-mcp): dcr can be toggled on/off
- feat(keycardai-mcp): private key jwt support with global key
- feat(keycardai-mcp): grant decorator exception handling
- feat(keycardai-mcp): private key manager protocol

## 0.4.1-keycardai-mcp (2025-09-18)


- fix(keycardai-mcp): support both sync and async tool calls

## 0.4.0-keycardai-mcp (2025-09-18)


- feat(keycardai-mcp): default domain handling

## 0.3.1-keycardai-mcp (2025-09-17)


- fix(keycardai-mcp): check audience when configured

## 0.3.0-keycardai-mcp (2025-09-16)


- feat(keycardai-mcp): multi-zone mcp routing
- feat(keycardai-mcp): advanced server handlers

## 0.2.0-keycardai-mcp (2025-09-16)


- feat(keycardai-mcp): auth provider implementation

## 0.1.0-keycardai-mcp (2025-09-07)
