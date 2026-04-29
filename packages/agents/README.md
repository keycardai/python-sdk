# keycardai-agents (deprecated, pending archive)

This package is being archived. Per the KEP "Decompose keycardai-agents", everything that lived here has moved:

| Old import | New import |
| --- | --- |
| `from keycardai.agents.server import AgentServer, ...` | `from keycardai.a2a import ...` (#105 / ACC-230) |
| `from keycardai.agents.client.discovery import ServiceDiscovery` | `from keycardai.a2a import ServiceDiscovery` (#105 / ACC-230) |
| `from keycardai.agents.client import AgentClient` | `from keycardai.oauth.pkce import authenticate` (#101 / ACC-229) |
| `from keycardai.agents.integrations.crewai import CrewAIExecutor, get_a2a_tools, ...` | `from keycardai.crewai import ...` (this PR / ACC-231) |

`keycardai-agents` no longer ships any code. It exists at this version only to give downstream installs a clean upgrade path. The next step (ACC-232) archives the package source entirely.

If you are starting fresh: skip `keycardai-agents` and depend on the destination packages directly.
