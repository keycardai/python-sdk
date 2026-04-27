## 0.2.0-keycardai-agents (2026-04-27)


- fix(keycardai-agents): restore test suite (#100)
- Closes ACC-236. Surfaced by ACC-234 wait-and-see verification: keycardai-agents tests would not run on a fresh checkout.
- Three pre-existing breaks fixed:
- 1. a2a-sdk constraint pinned to <1.0. The unbounded >=0.3.22 resolved to 1.0.x today; a2a-sdk 1.0 moved A2AStarletteApplication out of a2a.server.apps.jsonrpc, which keycardai-agents/server/app.py imports. Pinning is the cheap fix because keycardai-agents is being decomposed and archived in ACC-229..232; the replacement keycardai-a2a package will be written against a2a-sdk 1.x natively.
- 2. tests/integrations/test_crewai_a2a.py imported from keycardai.agents.integrations.crewai_a2a, which does not exist. The module is keycardai.agents.integrations.crewai. Three import/patch references updated.
- 3. test_tool_run_with_task_and_inputs asserted "pr_number" was a top-level key in the task dict; the actual contract puts task_inputs under task["inputs"]. Assertion updated to match the implementation.
- Verified: 85/85 agents tests pass. mcp 560/560, starlette 49/49, oauth 208/208 unaffected.

## 0.1.1-keycardai-agents (2026-01-07)
