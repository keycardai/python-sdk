"""CrewAI integration for A2A (agent-to-agent) delegation.

This module provides:
1. CrewAIExecutor: Adapter for running CrewAI crews in the agent service server
2. Delegation tools: CrewAI tools for calling other agent services

Usage with executor:
    >>> from keycardai.agents import AgentServiceConfig
    >>> from keycardai.agents.integrations.crewai import CrewAIExecutor
    >>> from crewai import Agent, Crew, Task
    >>>
    >>> def create_my_crew():
    ...     agent = Agent(role="Assistant", goal="Help users")
    ...     task = Task(description="{task}", agent=agent)
    ...     return Crew(agents=[agent], tasks=[task])
    >>>
    >>> config = AgentServiceConfig(
    ...     service_name="My Service",
    ...     agent_executor=CrewAIExecutor(create_my_crew),
    ...     # ... other config
    ... )

Usage with delegation tools:
    >>> from keycardai.agents import AgentServiceConfig
    >>> from keycardai.agents.integrations.crewai import get_a2a_tools
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

import contextvars
import logging
from typing import Any, Callable

from pydantic import BaseModel, Field

from ..client.discovery import ServiceDiscovery
from ..config import AgentServiceConfig
from ..server.delegation import DelegationClientSync

# Context variable to store the current user's access token for delegation
_current_user_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_token", default=None
)

try:
    from crewai import Crew
    from crewai.tools import BaseTool
except ImportError:
    raise ImportError(
        "CrewAI is not installed. Install it with: pip install 'keycardai-agents[crewai]'"
    ) from None

logger = logging.getLogger(__name__)


def set_delegation_token(access_token: str) -> None:
    """Set the user's access token for delegation context.

    This should be called before crew execution to provide the user's
    token for service-to-service delegation. The token will be used
    for token exchange when delegating to other services.

    Args:
        access_token: The user's access token from the request

    Example:
        >>> # In your server's invoke handler
        >>> access_token = request.state.keycardai_auth_info.get("access_token")
        >>> set_delegation_token(access_token)
        >>>
        >>> # Now crew tools can delegate with the user's context
        >>> crew = create_my_crew()
        >>> result = crew.kickoff()
    """
    _current_user_token.set(access_token)


class CrewAIExecutor:
    """Executor adapter for CrewAI crews.

    This executor implements the AgentExecutor protocol for CrewAI crews,
    allowing them to be used in the generic agent service server.

    The executor:
    1. Takes a crew factory callable
    2. Sets delegation token context before execution
    3. Calls crew.kickoff() with the task/inputs
    4. Returns the result as a string

    Args:
        crew_factory: Callable that returns a Crew instance
        set_token_context: If True, automatically set delegation token before execution

    Example:
        >>> from crewai import Agent, Crew, Task
        >>>
        >>> def create_my_crew():
        ...     agent = Agent(role="Assistant", goal="Help users", backstory="Helpful AI")
        ...     task = Task(description="{task}", agent=agent, expected_output="A response")
        ...     return Crew(agents=[agent], tasks=[task])
        >>>
        >>> executor = CrewAIExecutor(create_my_crew)
        >>> result = executor.execute("Hello world", {"name": "Alice"})
    """

    def __init__(self, crew_factory: Callable[[], Crew], set_token_context: bool = True):
        """Initialize CrewAI executor.

        Args:
            crew_factory: Callable that returns a Crew instance
            set_token_context: If True, automatically set delegation token before execution
        """
        self.crew_factory = crew_factory
        self.set_token_context = set_token_context

    def execute(
        self,
        task: dict[str, Any] | str,
        inputs: dict[str, Any] | None = None,
    ) -> str:
        """Execute crew with the given task and inputs.

        Args:
            task: Task description (string) or parameters (dict)
            inputs: Optional additional inputs for the crew

        Returns:
            Result from crew execution as string

        Raises:
            Exception: If crew execution fails
        """
        # Create crew instance
        crew = self.crew_factory()

        # Prepare inputs for crew
        if isinstance(task, dict):
            crew_inputs = task
        else:
            crew_inputs = {"task": task}

        # Merge additional inputs if provided
        if inputs:
            crew_inputs.update(inputs)

        # Execute crew
        # Note: crew.kickoff() is synchronous in CrewAI
        logger.info(f"Executing CrewAI crew with inputs: {list(crew_inputs.keys())}")
        result = crew.kickoff(inputs=crew_inputs)

        # Return result as string
        return str(result)

    def set_token_for_delegation(self, access_token: str) -> None:
        """Set access token for delegation context.

        This is called by the server before execution to provide
        the user's token for service-to-service delegation.

        Args:
            access_token: User's access token
        """
        if self.set_token_context:
            set_delegation_token(access_token)


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
