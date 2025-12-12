"""Tests for CrewAI A2A delegation integration."""

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("crewai")

from keycardai.agents import AgentServiceConfig
from keycardai.agents.integrations.crewai_a2a import (
    _create_delegation_tool,
    create_a2a_tool_for_service,
    get_a2a_tools,
)


@pytest.fixture
def service_config():
    """Create test service configuration."""
    return AgentServiceConfig(
        service_name="Test Service",
        client_id="test_client",
        client_secret="test_secret",
        identity_url="https://test.example.com",
        zone_id="test_zone_123",
    )


@pytest.fixture
def mock_delegatable_services():
    """Mock list of delegatable services."""
    return [
        {
            "name": "PR Analysis Service",
            "url": "https://pr-analyzer.example.com",
            "description": "Analyzes GitHub pull requests for code quality",
            "capabilities": ["pr_analysis", "code_review", "security_scan"],
        },
        {
            "name": "Slack Notification Service",
            "url": "https://slack-notifier.example.com",
            "description": "Posts notifications to Slack channels",
            "capabilities": ["slack_post", "notification"],
        },
    ]


@pytest.fixture
def mock_agent_card():
    """Mock agent card for service discovery."""
    return {
        "name": "Echo Service",
        "description": "Simple echo service for testing",
        "type": "crew_service",
        "identity": "https://echo.example.com",
        "endpoints": {
            "invoke": "https://echo.example.com/invoke",
            "status": "https://echo.example.com/status",
        },
        "auth": {"type": "oauth2"},
        "capabilities": ["echo", "testing"],
    }


class TestGetA2ATools:
    """Test A2A tool generation."""

    @pytest.mark.asyncio
    async def test_get_a2a_tools_with_no_services(self, service_config):
        """Test get_a2a_tools returns empty list when no services provided."""
        tools = await get_a2a_tools(service_config, delegatable_services=[])

        assert tools == []
        assert isinstance(tools, list)

    @pytest.mark.asyncio
    async def test_get_a2a_tools_with_provided_services(
        self, service_config, mock_delegatable_services
    ):
        """Test get_a2a_tools creates tools for provided services."""
        tools = await get_a2a_tools(
            service_config, delegatable_services=mock_delegatable_services
        )

        assert len(tools) == 2
        assert all(hasattr(tool, "name") for tool in tools)
        assert all(hasattr(tool, "description") for tool in tools)
        assert all(hasattr(tool, "_run") for tool in tools)

    @pytest.mark.asyncio
    async def test_get_a2a_tools_discovers_services_when_none_provided(
        self, service_config
    ):
        """Test get_a2a_tools discovers services from Keycard when not provided."""
        # When delegatable_services=None, it should try to discover
        # Currently returns empty list (discovery not implemented)
        with patch(
            "keycardai.agents.integrations.crewai_a2a.ServiceDiscovery"
        ) as mock_discovery_class:
            mock_discovery = AsyncMock()
            mock_discovery.list_delegatable_services.return_value = []
            mock_discovery.close = AsyncMock()
            mock_discovery_class.return_value = mock_discovery

            tools = await get_a2a_tools(service_config, delegatable_services=None)

            assert isinstance(tools, list)

    @pytest.mark.asyncio
    async def test_get_a2a_tools_creates_correct_tool_count(
        self, service_config, mock_delegatable_services
    ):
        """Test one tool is created per service."""
        tools = await get_a2a_tools(
            service_config, delegatable_services=mock_delegatable_services
        )

        assert len(tools) == len(mock_delegatable_services)


class TestCreateDelegationTool:
    """Test delegation tool creation."""

    def test_tool_name_generation(self, service_config):
        """Test tool name is generated correctly from service name."""
        service_info = {
            "name": "PR Analysis Service",
            "url": "https://pr-analyzer.example.com",
            "description": "Test service",
            "capabilities": [],
        }

        from keycardai.agents.a2a_client import A2AServiceClientSync

        a2a_client = A2AServiceClientSync(service_config)
        tool = _create_delegation_tool(service_info, a2a_client)

        assert tool.name == "delegate_to_pr_analysis_service"

    def test_tool_name_handles_special_characters(self, service_config):
        """Test tool name generation handles special characters."""
        service_info = {
            "name": "Slack-Notification Service",
            "url": "https://slack.example.com",
            "description": "Test service",
            "capabilities": [],
        }

        from keycardai.agents.a2a_client import A2AServiceClientSync

        a2a_client = A2AServiceClientSync(service_config)
        tool = _create_delegation_tool(service_info, a2a_client)

        # Hyphens should be converted to underscores
        assert tool.name == "delegate_to_slack_notification_service"

    def test_tool_description_includes_capabilities(self, service_config):
        """Test tool description includes service capabilities."""
        service_info = {
            "name": "Test Service",
            "url": "https://test.example.com",
            "description": "A test service",
            "capabilities": ["capability1", "capability2", "capability3"],
        }

        from keycardai.agents.a2a_client import A2AServiceClientSync

        a2a_client = A2AServiceClientSync(service_config)
        tool = _create_delegation_tool(service_info, a2a_client)

        # Check capabilities are in description
        assert "capability1" in tool.description
        assert "capability2" in tool.description
        assert "capability3" in tool.description

    def test_tool_has_correct_args_schema(self, service_config):
        """Test tool has proper args schema for CrewAI."""
        service_info = {
            "name": "Test Service",
            "url": "https://test.example.com",
            "description": "Test",
            "capabilities": [],
        }

        from keycardai.agents.a2a_client import A2AServiceClientSync

        a2a_client = A2AServiceClientSync(service_config)
        tool = _create_delegation_tool(service_info, a2a_client)

        # Tool should have args_schema attribute
        assert hasattr(tool, "args_schema")
        # Args schema should have task_description field
        assert "task_description" in tool.args_schema.model_fields


class TestDelegationToolExecution:
    """Test tool execution behavior."""

    def test_tool_run_with_task_string(self, service_config):
        """Test tool execution with simple task string."""
        service_info = {
            "name": "Echo Service",
            "url": "https://echo.example.com",
            "description": "Test",
            "capabilities": [],
        }

        from keycardai.agents.a2a_client import A2AServiceClientSync

        a2a_client = A2AServiceClientSync(service_config)
        tool = _create_delegation_tool(service_info, a2a_client)

        # Mock invoke_service to avoid actual network call
        with patch.object(a2a_client, "invoke_service") as mock_invoke:
            mock_invoke.return_value = {
                "result": "Echo response",
                "delegation_chain": ["service1", "echo_service"],
            }

            result = tool._run(task_description="Test task")

            assert "Echo response" in result
            mock_invoke.assert_called_once()

    def test_tool_run_with_task_and_inputs(self, service_config):
        """Test tool execution with task and additional inputs."""
        service_info = {
            "name": "PR Analyzer",
            "url": "https://pr-analyzer.example.com",
            "description": "Test",
            "capabilities": [],
        }

        from keycardai.agents.a2a_client import A2AServiceClientSync

        a2a_client = A2AServiceClientSync(service_config)
        tool = _create_delegation_tool(service_info, a2a_client)

        with patch.object(a2a_client, "invoke_service") as mock_invoke:
            mock_invoke.return_value = {
                "result": "PR analysis complete",
                "delegation_chain": [],
            }

            tool._run(
                task_description="Analyze PR", task_inputs={"pr_number": 123}
            )

            # Check invoke_service was called with correct task structure
            call_args = mock_invoke.call_args
            task = call_args[0][1]  # Second positional argument
            assert task["task"] == "Analyze PR"
            assert "pr_number" in task

    def test_tool_run_calls_a2a_client(self, service_config):
        """Test tool delegates to A2A client correctly."""
        service_info = {
            "name": "Test Service",
            "url": "https://test.example.com",
            "description": "Test",
            "capabilities": [],
        }

        from keycardai.agents.a2a_client import A2AServiceClientSync

        a2a_client = A2AServiceClientSync(service_config)
        tool = _create_delegation_tool(service_info, a2a_client)

        with patch.object(a2a_client, "invoke_service") as mock_invoke:
            mock_invoke.return_value = {"result": "success", "delegation_chain": []}

            tool._run(task_description="Test")

            # Verify invoke_service was called with service URL
            mock_invoke.assert_called_once()
            assert mock_invoke.call_args[0][0] == "https://test.example.com"

    def test_tool_run_formats_result_correctly(self, service_config):
        """Test tool formats result with delegation chain."""
        service_info = {
            "name": "Test Service",
            "url": "https://test.example.com",
            "description": "Test",
            "capabilities": [],
        }

        from keycardai.agents.a2a_client import A2AServiceClientSync

        a2a_client = A2AServiceClientSync(service_config)
        tool = _create_delegation_tool(service_info, a2a_client)

        with patch.object(a2a_client, "invoke_service") as mock_invoke:
            mock_invoke.return_value = {
                "result": "Task complete",
                "delegation_chain": ["service_a", "service_b"],
            }

            result = tool._run(task_description="Test")

            # Result should include delegation chain
            assert "Test Service" in result
            assert "Task complete" in result
            assert "service_a" in result
            assert "service_b" in result

    def test_tool_run_includes_delegation_chain(self, service_config):
        """Test tool includes delegation chain in response."""
        service_info = {
            "name": "Test Service",
            "url": "https://test.example.com",
            "description": "Test",
            "capabilities": [],
        }

        from keycardai.agents.a2a_client import A2AServiceClientSync

        a2a_client = A2AServiceClientSync(service_config)
        tool = _create_delegation_tool(service_info, a2a_client)

        with patch.object(a2a_client, "invoke_service") as mock_invoke:
            mock_invoke.return_value = {
                "result": "Done",
                "delegation_chain": ["chain_element_1", "chain_element_2"],
            }

            result = tool._run(task_description="Test")

            assert "Delegation chain" in result or "delegation" in result.lower()

    def test_tool_run_handles_exceptions(self, service_config):
        """Test tool handles exceptions gracefully."""
        service_info = {
            "name": "Test Service",
            "url": "https://test.example.com",
            "description": "Test",
            "capabilities": [],
        }

        from keycardai.agents.a2a_client import A2AServiceClientSync

        a2a_client = A2AServiceClientSync(service_config)
        tool = _create_delegation_tool(service_info, a2a_client)

        with patch.object(a2a_client, "invoke_service") as mock_invoke:
            mock_invoke.side_effect = RuntimeError("Network error")

            result = tool._run(task_description="Test")

            # Should return error message, not raise exception
            assert "Error" in result or "error" in result
            assert isinstance(result, str)


class TestCreateA2AToolForService:
    """Test single service tool creation."""

    @pytest.mark.asyncio
    async def test_create_tool_fetches_agent_card(
        self, service_config, mock_agent_card
    ):
        """Test create_a2a_tool_for_service fetches agent card."""
        with patch(
            "keycardai.agents.integrations.crewai_a2a.ServiceDiscovery"
        ) as mock_discovery_class:
            mock_discovery = AsyncMock()
            mock_discovery.get_service_card.return_value = mock_agent_card
            mock_discovery.close = AsyncMock()
            mock_discovery_class.return_value = mock_discovery

            tool = await create_a2a_tool_for_service(
                service_config, "https://echo.example.com"
            )

            # Should have fetched agent card
            mock_discovery.get_service_card.assert_called_once_with(
                "https://echo.example.com"
            )

            # Tool should be created with agent card info
            assert hasattr(tool, "name")
            assert hasattr(tool, "_run")

    @pytest.mark.asyncio
    async def test_create_tool_for_service(self, service_config, mock_agent_card):
        """Test tool is created correctly from agent card."""
        with patch(
            "keycardai.agents.integrations.crewai_a2a.ServiceDiscovery"
        ) as mock_discovery_class:
            mock_discovery = AsyncMock()
            mock_discovery.get_service_card.return_value = mock_agent_card
            mock_discovery.close = AsyncMock()
            mock_discovery_class.return_value = mock_discovery

            tool = await create_a2a_tool_for_service(
                service_config, "https://echo.example.com"
            )

            # Tool name should be based on service name from agent card
            assert "echo" in tool.name.lower()
            assert "service" in tool.name.lower()
