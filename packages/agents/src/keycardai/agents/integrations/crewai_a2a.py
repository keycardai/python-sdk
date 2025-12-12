"""CrewAI integration for A2A (agent-to-agent) delegation.

This module extends the base CrewAI MCP integration to add service-to-service
delegation capabilities. It provides tools that allow CrewAI agents to
delegate tasks to other agent services.

Usage:
    from keycardai.agents.integrations.crewai_a2a import extend_crewai_client_with_a2a
    from keycardai.mcp.client.integrations.crewai_agents import create_client
    from keycardai.agents import AgentServiceConfig

    # Create service config
    config = AgentServiceConfig(...)

    # Get MCP client with A2A tools
    async with create_client(mcp_client) as crew_client:
        mcp_tools = await crew_client.get_tools()

        # Add A2A delegation tools
        a2a_tools = await get_a2a_tools(crew_client, config)

        # Use all tools in crew
        agent = Agent(
            role="Orchestrator",
            tools=mcp_tools + a2a_tools
        )
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

try:
    from crewai.tools import BaseTool
except ImportError:
    raise ImportError(
        "CrewAI is not installed. Install it with: pip install 'keycardai-agents[crewai]'"
    ) from None

from ..a2a_client import A2AServiceClientSync
from ..discovery import ServiceDiscovery
from ..service_config import AgentServiceConfig

logger = logging.getLogger(__name__)


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

    # Create A2A client for delegation (synchronous to avoid event loop issues)
    a2a_client = A2AServiceClientSync(service_config)

    # Create tools for each service
    tools = []
    for service_info in delegatable_services:
        tool = _create_delegation_tool(service_info, a2a_client)
        tools.append(tool)

    logger.info(f"Created {len(tools)} A2A delegation tools")
    return tools


def _create_delegation_tool(
    service_info: dict[str, Any],
    a2a_client: A2AServiceClientSync,
) -> BaseTool:
    """Create a CrewAI tool for delegating to a specific service.

    Args:
        service_info: Service metadata (name, url, description, capabilities)
        a2a_client: A2A client for service invocation

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
            a2a_client: A2AServiceClientSync,
            service_url: str,
            service_name: str,
            **kwargs,
        ):
            super().__init__(**kwargs)
            self._a2a_client = a2a_client
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

                # Call remote service (token is obtained automatically)
                logger.info(
                    f"Delegating task to {self._service_name}: {task_description[:100]}"
                )

                result = self._a2a_client.invoke_service(
                    self._service_url,
                    task,
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
        a2a_client=a2a_client,
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

    # Create A2A client (synchronous to avoid event loop issues)
    a2a_client = A2AServiceClientSync(service_config)

    # Create and return tool
    return _create_delegation_tool(service_info, a2a_client)
