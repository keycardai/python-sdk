"""
Integration tests for agent framework integrations (OpenAI agents, LangChain).

Tests the agent adapters with a real MCP server:
1. OpenAI agents integration
2. LangChain agents integration

Each test:
- Uses LocalAuthCoordinator for simple browser-based OAuth
- Creates a real agent instance (OpenAI Agent or LangChain AgentExecutor)
- Let the agent call MCP tools through the integration
- Verifies the agent response

**These tests are SKIPPED by default** because they require manual OAuth interaction
and API keys.

To run them:
    export KEYCARD_ZONE_URL="https://your-zone.keycard.cloud"
    export OPENAI_API_KEY="sk-..."
    export RUN_INTERACTIVE_TESTS=1

    # Run all integration tests
    uv run pytest packages/mcp/tests/integration/test_agent_integrations.py -v -s

    # Run only OpenAI test
    uv run pytest packages/mcp/tests/integration/test_agent_integrations.py::TestOpenAIAgentsIntegration -v -s

    # Run only LangChain test
    uv run pytest packages/mcp/tests/integration/test_agent_integrations.py::TestLangChainAgentsIntegration -v -s
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from agents import Agent, Runner
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from keycardai.mcp.client import Client, InMemoryBackend, LocalAuthCoordinator
from keycardai.mcp.client.integrations.langchain_agents import LangChainClient
from keycardai.mcp.client.integrations.openai_agents import OpenAIAgentsClient

if TYPE_CHECKING:
    from .conftest import MCPServerFixture

# Skip interactive tests by default unless explicitly enabled
RUN_INTERACTIVE_TESTS = os.getenv("RUN_INTERACTIVE_TESTS", "0") == "1"
skip_manual = pytest.mark.skipif(
    not RUN_INTERACTIVE_TESTS,
    reason="Interactive integration tests disabled by default. Set RUN_INTERACTIVE_TESTS=1 to run them.",
)

# Note: Shared fixtures (mcp_server, storage_backend, etc.) are defined in conftest.py


@skip_manual
class TestOpenAIAgentsIntegration:
    """
    Integration tests for OpenAI agents SDK integration.

    Tests that the OpenAI agents adapter correctly:
    - Wraps MCP client
    - Provides MCP servers to agent
    - Allows agent to call MCP tools
    - Returns correct responses

    To run this test:
    1. Set KEYCARD_ZONE_URL environment variable
    2. Set RUN_INTERACTIVE_TESTS=1
    3. Run: uv run pytest packages/mcp/tests/integration/test_agent_integrations.py::TestOpenAIAgentsIntegration -v -s
    4. Browser will open for OAuth approval
    5. Complete the OAuth flow
    6. Test will verify agent can call MCP tool
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(360)  # 6 minutes: 5 min OAuth wait + 1 min buffer
    async def test_openai_agent_calls_mcp_tool(
        self, mcp_server: MCPServerFixture, storage_backend: InMemoryBackend
    ):
        """Test OpenAI agent calling MCP tools through the adapter."""
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            pytest.skip("OPENAI_API_KEY environment variable not set")

        print(f"\n{'='*70}")
        print(f"MCP Server running at: {mcp_server.url}")
        print("Testing OpenAI agents integration")
        print("When browser opens, please complete the OAuth authorization.")
        print(f"{'='*70}\n")

        servers = {
            "test": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {"type": "oauth"},
            }
        }

        coordinator = LocalAuthCoordinator(
            backend=storage_backend,
            host="localhost",
            port=0,  # Auto-assign port
            callback_path="/oauth/callback",
        )

        async with Client(
            servers,
            auth_coordinator=coordinator,
            storage_backend=storage_backend
        ) as mcp_client:
            print("✓ MCP Client connected and authenticated")

            async with OpenAIAgentsClient(mcp_client) as openai_client:
                print("✓ OpenAI agents client adapter created")

                system_prompt = "You are a helpful assistant that can use tools to help users."
                full_prompt = openai_client.get_system_prompt(system_prompt)
                assert "AUTH" not in full_prompt.upper() or "already have access" in full_prompt
                print("✓ System prompt ready")

                # Get MCP servers for agent
                mcp_servers = openai_client.get_mcp_servers()
                assert len(mcp_servers) > 0, "Should have authenticated MCP servers"
                assert mcp_servers[0].name == "test"
                print(f"✓ Got {len(mcp_servers)} MCP server(s)")

                # Get auth tools (should be empty since authenticated)
                auth_tools = openai_client.get_auth_tools()
                assert len(auth_tools) == 0, "Should have no auth tools when authenticated"
                print("✓ No auth tools needed (already authenticated)")

                # Create OpenAI Agent with MCP servers
                print("\n--- Creating OpenAI Agent ---")
                agent = Agent(
                    name="test_agent",
                    instructions=full_prompt,
                    mcp_servers=mcp_servers,
                    tools=auth_tools,
                )
                print("✓ OpenAI Agent created with MCP servers")

                # Test: Let the agent call the echo tool
                print("\n--- Testing Agent Tool Call (echo) ---")
                user_message = "Please use the echo tool to echo back 'Hello from integration test'"

                print(f"  User: {user_message}")
                response = await Runner.run(agent, user_message)

                print("✓ Agent executed successfully")
                print(f"  Agent response: {response.final_output}")

                # Verify the agent called the tool and got a response
                assert response.final_output is not None, "Agent should return a response"
                assert len(response.final_output) > 0, "Agent response should not be empty"

                # The response should contain the echo message
                # Note: The exact format depends on how the agent phrases it
                assert "Hello from integration test" in response.final_output or "integration test" in response.final_output.lower(), \
                    f"Expected echo content in agent response, got: {response.final_output}"

                print("✓ Agent successfully called echo tool and returned response")

                # Test: Let the agent call the add_numbers tool
                print("\n--- Testing Agent Tool Call (add_numbers) ---")
                user_message2 = "Please use the add_numbers tool to add 15 and 27"

                print(f"  User: {user_message2}")
                response2 = await Runner.run(agent, user_message2)

                print("✓ Agent executed successfully")
                print(f"  Agent response: {response2.final_output}")

                # Verify the agent got the correct result
                assert response2.final_output is not None, "Agent should return a response"
                # The answer should be 42
                assert "42" in response2.final_output, \
                    f"Expected '42' in agent response, got: {response2.final_output}"

                print("✓ Agent successfully called add_numbers tool and returned correct result")

                print(f"\n{'='*70}")
                print("✅ OpenAI AGENTS INTEGRATION TEST PASSED")
                print(f"{'='*70}")
                print("Summary:")
                print("  - MCP client connected with OAuth")
                print("  - OpenAI agents adapter created")
                print("  - OpenAI Agent instantiated with MCP servers")
                print("  - Agent called echo tool successfully")
                print("  - Agent called add_numbers tool successfully")
                print("  - Agent responses verified correctly")
                print(f"{'='*70}\n")


@skip_manual
class TestLangChainAgentsIntegration:
    """
    Integration tests for LangChain agents integration.

    Tests that the LangChain adapter correctly:
    - Wraps MCP client
    - Converts MCP tools to LangChain tools
    - Allows agent to call MCP tools
    - Returns correct responses

    To run this test:
    1. Set KEYCARD_ZONE_URL environment variable
    2. Set RUN_INTERACTIVE_TESTS=1
    3. Run: uv run pytest packages/mcp/tests/integration/test_agent_integrations.py::TestLangChainAgentsIntegration -v -s
    4. Browser will open for OAuth approval
    5. Complete the OAuth flow
    6. Test will verify agent can call MCP tool
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(360)  # 6 minutes: 5 min OAuth wait + 1 min buffer
    async def test_langchain_agent_calls_mcp_tool(
        self, mcp_server: MCPServerFixture, storage_backend: InMemoryBackend
    ):
        """Test LangChain agent calling MCP tools through the adapter."""
        # Require OpenAI API key (LangChain can use OpenAI models)
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            pytest.skip("OPENAI_API_KEY environment variable not set")

        print(f"\n{'='*70}")
        print(f"MCP Server running at: {mcp_server.url}")
        print("Testing LangChain agents integration")
        print("When browser opens, please complete the OAuth authorization.")
        print(f"{'='*70}\n")

        # Configure servers
        servers = {
            "test": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {"type": "oauth"},
            }
        }

        # Create coordinator with local callback server
        coordinator = LocalAuthCoordinator(
            backend=storage_backend,
            host="localhost",
            port=0,  # Auto-assign port
            callback_path="/oauth/callback",
        )

        # Create MCP client
        async with Client(
            servers,
            auth_coordinator=coordinator,
            storage_backend=storage_backend
        ) as mcp_client:
            print("✓ MCP Client connected and authenticated")

            # Create LangChain client adapter
            async with LangChainClient(mcp_client) as langchain_client:
                print("✓ LangChain client adapter created")

                # Get system prompt (should not have auth warnings since we're authenticated)
                system_prompt = "You are a helpful assistant that can use tools to help users."
                full_prompt = langchain_client.get_system_prompt(system_prompt)
                assert "AUTH" not in full_prompt.upper() or "already have access" in full_prompt
                print("✓ System prompt ready")

                # Get MCP tools converted to LangChain tools
                tools = await langchain_client.get_tools()
                assert len(tools) > 0, "Should have MCP tools converted to LangChain tools"
                print(f"✓ Got {len(tools)} LangChain tool(s)")

                # Verify tools have expected structure
                tool_names = [tool.name for tool in tools]
                assert "echo" in tool_names, "Should have 'echo' tool"
                assert "add_numbers" in tool_names, "Should have 'add_numbers' tool"
                print(f"  Tools: {', '.join(tool_names)}")

                # Get auth tools (should be empty since authenticated)
                auth_tools = await langchain_client.get_auth_tools()
                assert len(auth_tools) == 0, "Should have no auth tools when authenticated"
                print("✓ No auth tools needed (already authenticated)")

                # Create LangChain agent
                print("\n--- Creating LangChain Agent ---")

                # Create LLM
                llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

                # Create agent using create_agent
                agent = create_agent(
                    llm,
                    tools=tools,
                    system_prompt=full_prompt,
                )

                print("✓ LangChain Agent created with MCP tools")

                # Test: Let the agent call the echo tool
                print("\n--- Testing Agent Tool Call (echo) ---")
                user_input = "Please use the echo tool to echo back 'Hello from LangChain test'"

                print(f"  User: {user_input}")
                response = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": user_input}]}
                )

                print("✓ Agent executed successfully")

                # Extract final response from messages
                assert response is not None, "Agent should return a response"
                assert "messages" in response, "Response should have messages key"
                assert len(response["messages"]) > 0, "Response should have messages"

                final_message = response["messages"][-1]
                output = final_message.content

                print(f"  Agent response: {output}")

                # Verify the response
                assert len(output) > 0, "Agent output should not be empty"

                # The response should contain the echo message
                assert "Hello from LangChain test" in output or "langchain test" in output.lower(), \
                    f"Expected echo content in agent response, got: {output}"

                print("✓ Agent successfully called echo tool and returned response")

                # Test: Let the agent call the add_numbers tool
                print("\n--- Testing Agent Tool Call (add_numbers) ---")
                user_input2 = "Please use the add_numbers tool to add 23 and 19"

                print(f"  User: {user_input2}")
                response2 = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": user_input2}]}
                )

                print("✓ Agent executed successfully")

                # Extract final response from messages
                final_message2 = response2["messages"][-1]
                output2 = final_message2.content

                print(f"  Agent response: {output2}")

                # Verify the agent got the correct result
                assert output2 is not None, "Agent should return a response"
                # The answer should be 42
                assert "42" in output2, \
                    f"Expected '42' in agent response, got: {output2}"

                print("✓ Agent successfully called add_numbers tool and returned correct result")

                print(f"\n{'='*70}")
                print("✅ LANGCHAIN INTEGRATION TEST PASSED")
                print(f"{'='*70}")
                print("Summary:")
                print("  - MCP client connected with OAuth")
                print("  - LangChain adapter created")
                print("  - LangChain Agent instantiated with MCP tools")
                print("  - Agent called echo tool successfully")
                print("  - Agent called add_numbers tool successfully")
                print("  - Agent responses verified correctly")
                print(f"{'='*70}\n")

