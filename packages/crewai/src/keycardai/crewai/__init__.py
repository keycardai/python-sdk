"""CrewAI integration for A2A (agent-to-agent) delegation.

Two halves:

1. ``CrewAIExecutor``: an ``a2a-sdk`` 1.x ``AgentExecutor`` subclass that runs
   a CrewAI ``Crew`` in response to incoming A2A messages. Pass it directly
   to ``a2a.server.request_handlers.DefaultRequestHandler`` — no wrapper
   needed.
2. Delegation tools: ``crewai.tools.BaseTool`` instances that let a CrewAI
   agent delegate work to other A2A services, exchanging the inbound user
   token via Keycard.

Usage with executor:
    >>> from a2a.server.request_handlers import DefaultRequestHandler
    >>> from a2a.server.tasks import InMemoryTaskStore
    >>> from keycardai.a2a import AgentServiceConfig, build_agent_card_from_config
    >>> from keycardai.crewai import CrewAIExecutor
    >>> from crewai import Agent, Crew, Task
    >>>
    >>> def create_my_crew():
    ...     agent = Agent(role="Assistant", goal="Help users")
    ...     task = Task(description="{task}", agent=agent)
    ...     return Crew(agents=[agent], tasks=[task])
    >>>
    >>> config = AgentServiceConfig(service_name="My Service", ...)
    >>> agent_card = build_agent_card_from_config(config)
    >>> request_handler = DefaultRequestHandler(
    ...     agent_executor=CrewAIExecutor(create_my_crew),
    ...     task_store=InMemoryTaskStore(),
    ...     agent_card=agent_card,
    ... )

Usage with delegation tools:
    >>> from keycardai.a2a import AgentServiceConfig
    >>> from keycardai.crewai import get_a2a_tools
    >>> from crewai import Agent, Crew
    >>>
    >>> # Create service config
    >>> config = AgentServiceConfig(...)
    >>>
    >>> # Define services we can delegate to
    >>> delegatable_services = [
    >>>     {
    >>>         "name": "echo_service",
    >>>         "url": "http://localhost:8002",
    >>>         "description": "Echo service that repeats messages",
    >>>     }
    >>> ]
    >>>
    >>> # Get A2A delegation tools
    >>> a2a_tools = await get_a2a_tools(config, delegatable_services)
    >>>
    >>> # Use tools in crew
    >>> agent = Agent(
    >>>     role="Orchestrator",
    >>>     tools=a2a_tools,
    >>>     allow_delegation=True
    >>> )
"""

import asyncio
import contextvars
import logging
from typing import Any, Callable

from a2a.server.agent_execution import AgentExecutor
from a2a.server.events.event_queue_v2 import EventQueue
from a2a.types import Message, Part, Role
from keycardai.a2a import AgentServiceConfig, DelegationClientSync, ServiceDiscovery
from pydantic import BaseModel, Field

from crewai import Crew
from crewai.tools import BaseTool

# Context variable to store the current user's access token for delegation.
# Read by ServiceDelegationTool._run; written by CrewAIExecutor.execute (or
# manually via set_delegation_token).
_current_user_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_token", default=None
)

logger = logging.getLogger(__name__)


def set_delegation_token(access_token: str) -> None:
    """Set the user's access token for delegation context.

    ``CrewAIExecutor`` calls this for you. Use it directly only when running
    a crew outside the executor (e.g., from a custom AgentExecutor or a
    test).

    Args:
        access_token: The user's access token from the request

    Example:
        >>> # In a custom AgentExecutor.execute method
        >>> access_token = context.call_context.state.get("access_token")
        >>> if access_token:
        ...     set_delegation_token(access_token)
        >>> result = my_crew.kickoff(...)
    """
    _current_user_token.set(access_token)


class CrewAIExecutor(AgentExecutor):
    """``a2a-sdk`` 1.x ``AgentExecutor`` that runs a CrewAI ``Crew``.

    Pass an instance directly to
    ``a2a.server.request_handlers.DefaultRequestHandler(agent_executor=...)``;
    no outer wrapper is needed. Subclasses ``a2a.server.agent_execution.AgentExecutor``
    so it satisfies the wire-up contract that ``DefaultRequestHandler`` expects.

    On each call to ``execute``:

    1. Reads ``context.call_context.state["access_token"]`` (populated by
       ``keycardai.a2a.KeycardServerCallContextBuilder``) and sets the
       delegation contextvar so synchronous CrewAI tools can pick it up.
    2. Calls the ``crew_factory`` to build a fresh ``Crew``.
    3. Runs ``crew.kickoff(inputs={"task": <user input>})`` on a worker thread
       via ``asyncio.to_thread`` so the synchronous CrewAI runtime does not
       starve uvicorn's event loop. ``asyncio.to_thread`` propagates the
       contextvar via ``contextvars.copy_context``; do **not** swap this for a
       raw ``ThreadPoolExecutor``, which would not, and would silently break
       delegation.
    4. Wraps the string result in an A2A ``Message`` and enqueues it.

    Args:
        crew_factory: Callable that returns a fresh ``Crew`` for each request.

    Example:
        >>> from crewai import Agent, Crew, Task
        >>>
        >>> def create_my_crew():
        ...     agent = Agent(role="Assistant", goal="Help users", backstory="Helpful AI")
        ...     task = Task(description="{task}", agent=agent, expected_output="A response")
        ...     return Crew(agents=[agent], tasks=[task])
        >>>
        >>> executor = CrewAIExecutor(create_my_crew)
    """

    def __init__(self, crew_factory: Callable[[], Crew]):
        self.crew_factory = crew_factory

    async def execute(self, context: Any, event_queue: EventQueue) -> None:
        call_ctx = getattr(context, "call_context", None)
        access_token = call_ctx.state.get("access_token") if call_ctx else None
        if access_token:
            set_delegation_token(access_token)
        else:
            logger.warning(
                "No access_token in RequestContext.call_context.state; "
                "delegation tools will run without a user token. Ensure the "
                "JSONRPC mount uses keycardai.a2a.KeycardServerCallContextBuilder."
            )

        user_input = context.get_user_input()
        crew = self.crew_factory()
        crew_inputs = {"task": user_input}

        logger.info("Executing CrewAI crew")
        result = await asyncio.to_thread(crew.kickoff, inputs=crew_inputs)

        message = Message(
            role=Role.ROLE_AGENT,
            parts=[Part(text=str(result))],
        )
        await event_queue.enqueue_event(message)

    async def cancel(self, context: Any, event_queue: EventQueue) -> None:
        return None


async def get_a2a_tools(
    service_config: AgentServiceConfig,
    delegatable_services: list[dict[str, Any]] | None = None,
) -> list[BaseTool]:
    """Get A2A delegation tools for CrewAI agents.

    Creates CrewAI tools that allow agents to delegate tasks to other
    agent services. Tools are automatically generated based on:
    1. Keycard dependencies (services this service can call)
    2. Agent card capabilities (what each service can do)

    Args:
        service_config: Configuration of the calling service
        delegatable_services: Optional list of services to create tools for.
            If not provided, queries Keycard for dependencies.
            Each service dict should have: name, url, description, capabilities

    Returns:
        List of CrewAI BaseTool objects for delegation

    Example:
        >>> config = AgentServiceConfig(...)
        >>> tools = await get_a2a_tools(config)
        >>> # Returns tools like:
        >>> # - delegate_to_slack_poster
        >>> # - delegate_to_deployment_service
        >>> agent = Agent(role="Orchestrator", tools=tools)
    """
    # Discover delegatable services if not provided
    if delegatable_services is None:
        discovery = ServiceDiscovery(service_config)
        try:
            delegatable_services = await discovery.list_delegatable_services()
        finally:
            await discovery.close()

    if not delegatable_services:
        logger.info("No delegatable services found - no A2A tools created")
        return []

    # Create delegation client for delegation (synchronous to avoid event loop issues)
    delegation_client = DelegationClientSync(service_config)

    # Create tools for each service
    tools = []
    for service_info in delegatable_services:
        tool = _create_delegation_tool(service_info, delegation_client)
        tools.append(tool)

    logger.info(f"Created {len(tools)} A2A delegation tools")
    return tools


def _create_delegation_tool(
    service_info: dict[str, Any],
    delegation_client: DelegationClientSync,
) -> BaseTool:
    """Create a CrewAI tool for delegating to a specific service.

    Args:
        service_info: Service metadata (name, url, description, capabilities)
        delegation_client: Delegation client for service invocation

    Returns:
        CrewAI BaseTool for delegation
    """
    service_name = service_info["name"]
    service_url = service_info["url"]
    service_description = service_info.get("description", "")
    capabilities = service_info.get("capabilities", [])

    # Generate tool name (e.g., "PR Analysis Service" -> "delegate_to_pr_analysis_service")
    tool_name = f"delegate_to_{service_name.lower().replace(' ', '_').replace('-', '_')}"

    # Generate tool description
    capabilities_str = ", ".join(capabilities) if capabilities else "various tasks"
    tool_description = f"""Delegate a task to {service_name}.

{service_description}

This service can handle: {capabilities_str}

Use this tool when you need {service_name} to perform a task that is within its capabilities.
The service will process the task and return results."""

    # Define the tool class
    class ServiceDelegationTool(BaseTool):
        """Tool for delegating to another agent service."""

        name: str = tool_name
        description: str = tool_description

        def __init__(
            self,
            delegation_client: DelegationClientSync,
            service_url: str,
            service_name: str,
            **kwargs,
        ):
            super().__init__(**kwargs)
            self._delegation_client = delegation_client
            self._service_url = service_url
            self._service_name = service_name

        def _run(self, task_description: str, task_inputs: dict[str, Any] | None = None) -> str:
            """Delegate task to remote service.

            Args:
                task_description: Description of the task to delegate
                task_inputs: Optional additional inputs for the task

            Returns:
                Result from the delegated service
            """
            try:
                # Prepare task
                task = {
                    "task": task_description,
                }
                if task_inputs:
                    task["inputs"] = task_inputs

                # Get user token from context for delegation
                user_token = _current_user_token.get()
                if not user_token:
                    logger.warning(
                        "No user token available for delegation - "
                        "ensure set_delegation_token() is called before crew execution"
                    )

                # Call remote service with user token for delegation
                logger.info(
                    f"Delegating task to {self._service_name}: {task_description[:100]}"
                )

                result = self._delegation_client.invoke_service(
                    self._service_url,
                    task,
                    subject_token=user_token,
                )

                # Format result for agent
                result_str = result.get("result", "")
                delegation_chain = result.get("delegation_chain", [])

                # Include delegation chain in response for transparency
                response = f"Result from {self._service_name}:\n\n{result_str}"

                if delegation_chain:
                    response += f"\n\n(Delegation chain: {' → '.join(delegation_chain)})"

                return response

            except Exception as e:
                logger.error(
                    f"Delegation to {self._service_name} failed: {e}",
                    exc_info=True,
                )
                return f"Error delegating to {self._service_name}: {str(e)}"

    # Create args schema
    class DelegationInput(BaseModel):
        """Input for service delegation tool."""

        task_description: str = Field(
            description=f"Description of the task to delegate to {service_name}"
        )
        task_inputs: dict[str, Any] | None = Field(
            default=None,
            description="Optional additional inputs/parameters for the task",
        )

    ServiceDelegationTool.args_schema = DelegationInput

    # Instantiate and return tool
    tool = ServiceDelegationTool(
        delegation_client=delegation_client,
        service_url=service_url,
        service_name=service_name,
    )

    return tool


# For manual service list specification (useful for testing)
async def create_a2a_tool_for_service(
    service_config: AgentServiceConfig,
    target_service_url: str,
) -> BaseTool:
    """Create a single A2A delegation tool for a specific service.

    Useful for testing or when you want to manually specify delegation targets.

    Args:
        service_config: Configuration of the calling service
        target_service_url: URL of the target service

    Returns:
        CrewAI BaseTool for delegation

    Example:
        >>> config = AgentServiceConfig(...)
        >>> tool = await create_a2a_tool_for_service(
        ...     config,
        ...     "https://slack-poster.example.com"
        ... )
        >>> agent = Agent(role="Orchestrator", tools=[tool])
    """
    # Discover the service
    discovery = ServiceDiscovery(service_config)
    try:
        card = await discovery.get_service_card(target_service_url)
    finally:
        await discovery.close()

    # Create service info dict
    service_info = {
        "name": card["name"],
        "url": target_service_url,
        "description": card.get("description", ""),
        "capabilities": card.get("capabilities", []),
    }

    # Create delegation client (synchronous to avoid event loop issues)
    delegation_client = DelegationClientSync(service_config)

    # Create and return tool
    return _create_delegation_tool(service_info, delegation_client)


__all__ = [
    "CrewAIExecutor",
    "create_a2a_tool_for_service",
    "get_a2a_tools",
    "set_delegation_token",
]
