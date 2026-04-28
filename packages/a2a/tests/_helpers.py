"""Shared test helpers for keycardai-a2a tests."""

from a2a.server.agent_execution import AgentExecutor


class NoopAgentExecutor(AgentExecutor):
    """Minimal a2a-sdk AgentExecutor for tests that need an executor instance.

    Tests that exercise routing, configuration, or auth wiring do not
    actually execute the agent; they just need a valid AgentExecutor to
    satisfy AgentServiceConfig. This subclass returns immediately on both
    execute() and cancel().
    """

    async def execute(self, context, event_queue) -> None:
        return None

    async def cancel(self, context, event_queue) -> None:
        return None
