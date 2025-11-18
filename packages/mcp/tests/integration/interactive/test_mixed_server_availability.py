"""
Integration test for handling mixed server availability scenarios.

Tests that the MCP client can gracefully handle situations where:
- Some servers are available and working
- Other servers are unavailable (unreachable)

The client should:
- Not throw exceptions for unavailable servers
- Use status lifecycle to communicate server state
- Allow operations on available servers to succeed
- Properly track status of each session independently

**This test is SKIPPED by default** because it requires manual OAuth interaction.

To run it:
    export KEYCARD_ZONE_URL="https://your-zone.keycard.cloud"
    export RUN_INTERACTIVE_TESTS=1

    # Run the test
    uv run pytest packages/mcp/tests/integration/interactive/test_mixed_server_availability.py -v -s
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest

from keycardai.mcp.client import (
    Client,
    InMemoryBackend,
    LocalAuthCoordinator,
    SessionStatus,
)
from keycardai.mcp.client.exceptions import ToolNotFoundException

if TYPE_CHECKING:
    from .conftest import MCPServerFixture

# Skip interactive tests by default unless explicitly enabled
RUN_INTERACTIVE_TESTS = os.getenv("RUN_INTERACTIVE_TESTS", "0") == "1"
skip_manual = pytest.mark.skipif(
    not RUN_INTERACTIVE_TESTS,
    reason="Interactive integration tests disabled by default. Set RUN_INTERACTIVE_TESTS=1 to run them.",
)


@skip_manual
class TestMixedServerAvailability:
    """
    Integration test for mixed server availability scenarios.

    Tests that the client can handle:
    - One working server (with OAuth)
    - One unavailable server (unreachable)

    Verifies that:
    - Client doesn't crash when one server is unavailable
    - Available server can be used normally
    - Each session maintains independent status
    - Status lifecycle properly tracks each server's state
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(360)  # 6 minutes: 5 min OAuth wait + 1 min buffer
    async def test_client_with_mixed_server_availability(
        self,
        mcp_server: MCPServerFixture,
        storage_backend: InMemoryBackend
    ):
        """
        Test client behavior when connecting to multiple servers with mixed availability.

        Scenario:
        1. Create client with 2 servers:
           - 'working_server': Real MCP server (requires OAuth)
           - 'unavailable_server': Invalid server (unreachable)
        2. Attempt to connect to both
        3. Verify working server connects successfully
        4. Verify unavailable server sets appropriate failure status
        5. Verify client can still use working server despite other failure
        """
        print(f"\n{'='*70}")
        print("Testing Mixed Server Availability")
        print(f"{'='*70}")
        print(f"Working server: {mcp_server.url}")
        print("Unavailable server: http://localhost:9999/mcp (intentionally wrong)")
        print("When browser opens, please complete the OAuth authorization.")
        print(f"{'='*70}\n")

        # Configure servers - one working, one unavailable
        servers = {
            "working_server": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {"type": "oauth"},
            },
            "unavailable_server": {
                # Use a port that's definitely not in use
                "url": "http://localhost:9999/mcp",
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

        # Create client with both servers
        client = Client(
            servers,
            auth_coordinator=coordinator,
            storage_backend=storage_backend
        )

        try:
            # Phase 1: Connect to both servers
            print("\n--- Phase 1: Connecting to servers ---")
            await client.connect()
            print("✓ Connection attempts completed (no exceptions thrown)")

            # Phase 2: Check status of each session
            print("\n--- Phase 2: Checking session statuses ---")

            working_session = client.sessions["working_server"]
            unavailable_session = client.sessions["unavailable_server"]

            print(f"  working_server status: {working_session.status.value}")
            print(f"  unavailable_server status: {unavailable_session.status.value}")

            # Verify working server is connected or requires auth
            assert working_session.status in {
                SessionStatus.CONNECTED,
                SessionStatus.AUTH_PENDING
            }, f"Working server should be connected or require auth, got: {working_session.status}"

            # If auth is pending, complete it
            if working_session.status == SessionStatus.AUTH_PENDING:
                print("\n--- OAuth Required for working_server ---")
                challenges = await client.get_auth_challenges()
                print(f"  Found {len(challenges)} auth challenge(s)")

                # Wait for OAuth to complete and retry connection
                print("  Waiting for OAuth completion...")
                await asyncio.sleep(2)  # Give time for OAuth
                await client.connect(server="working_server")

                print(f"  working_server status after retry: {working_session.status.value}")

            # Verify working server is now connected
            assert working_session.is_operational, \
                f"Working server should be operational, status: {working_session.status}"
            print("✓ Working server is CONNECTED and operational")

            # Verify unavailable server has appropriate failure status
            assert unavailable_session.is_failed, \
                f"Unavailable server should be in failure state, got: {unavailable_session.status}"
            assert unavailable_session.status in {
                SessionStatus.SERVER_UNREACHABLE,
                SessionStatus.CONNECTION_FAILED,
                SessionStatus.FAILED
            }, f"Unavailable server should have connection failure status, got: {unavailable_session.status}"
            print(f"✓ Unavailable server correctly marked as {unavailable_session.status.value}")

            # Verify unavailable server can be retried
            assert unavailable_session.can_retry or unavailable_session.is_failed, \
                "Unavailable server should be in retryable or failed state"
            print("✓ Unavailable server status indicates it can be retried")

            # Phase 3: Use working server despite unavailable server
            print("\n--- Phase 3: Using working server ---")

            # List tools from working server
            tools = await client.list_tools(server_name="working_server")
            assert len(tools) > 0, "Working server should have tools available"
            print(f"✓ Got {len(tools)} tool(s) from working server:")
            for tool_info in tools:
                print(f"    - {tool_info.tool.name}: {tool_info.tool.description}")

            # Call a tool on the working server
            print("\n  Testing tool call on working server...")
            result = await client.call_tool(
                server_name="working_server",
                tool_name="echo",
                arguments={"message": "Test message from mixed availability test"}
            )

            assert result is not None, "Tool call should return result"
            print(f"✓ Tool call succeeded: {result.content[0].text if result.content else 'No content'}")

            # Phase 4: Verify error handling for unavailable server
            print("\n--- Phase 4: Verify unavailable server behavior ---")

            try:
                await client.call_tool(
                    server_name="unavailable_server",
                    tool_name="echo",
                    arguments={"message": "This should fail"}
                )
                pytest.fail("Should not be able to call tools on unavailable server")
            except ToolNotFoundException as e:
                print(f"✓ Unavailable server correctly raises ToolNotFoundException: {e}")

            # Phase 5: Summary
            print(f"\n{'='*70}")
            print("✅ MIXED SERVER AVAILABILITY TEST PASSED")
            print(f"{'='*70}")
            print("Summary:")
            print("  - Client created with 2 servers")
            print(f"  - working_server: {working_session.status.value}")
            print(f"  - unavailable_server: {unavailable_session.status.value}")
            print("  - Client successfully used working server")
            print("  - Client gracefully handled unavailable server")
            print("  - No exceptions thrown during connection attempts")
            print("  - Each session maintained independent status")
            print(f"{'='*70}\n")

        finally:
            # Cleanup
            await client.disconnect()
            await coordinator.shutdown()

    @pytest.mark.asyncio
    @pytest.mark.timeout(360)  # 6 minutes: 5 min OAuth wait + 1 min buffer
    async def test_client_with_mixed_server_availability_context_manager(
        self,
        mcp_server: MCPServerFixture,
        storage_backend: InMemoryBackend
    ):
        """
        Test client behavior with mixed availability using async context manager.

        Same scenario as test_client_with_mixed_server_availability but demonstrates
        the cleaner async with pattern for resource management.

        Scenario:
        1. Create client with 2 servers:
           - 'working_server': Real MCP server (requires OAuth)
           - 'unavailable_server': Invalid server (unreachable)
        2. Attempt to connect to both
        3. Verify working server connects successfully
        4. Verify unavailable server sets appropriate failure status
        5. Verify client can still use working server despite other failure
        """
        print(f"\n{'='*70}")
        print("Testing Mixed Server Availability (Context Manager)")
        print(f"{'='*70}")
        print(f"Working server: {mcp_server.url}")
        print("Unavailable server: http://localhost:9999/mcp (intentionally wrong)")
        print("When browser opens, please complete the OAuth authorization.")
        print(f"{'='*70}\n")

        # Configure servers - one working, one unavailable
        servers = {
            "working_server": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {"type": "oauth"},
            },
            "unavailable_server": {
                # Use a port that's definitely not in use
                "url": "http://localhost:9999/mcp",
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

        # Use async context manager for automatic cleanup
        async with Client(
            servers,
            auth_coordinator=coordinator,
            storage_backend=storage_backend
        ) as client:
            # Phase 1: Check connection status (context manager auto-connects)
            print("\n--- Phase 1: Checking connection status ---")
            print("✓ Client connected (context manager auto-connects)")

            # Phase 2: Check status of each session
            print("\n--- Phase 2: Checking session statuses ---")

            working_session = client.sessions["working_server"]
            unavailable_session = client.sessions["unavailable_server"]

            print(f"  working_server status: {working_session.status.value}")
            print(f"  unavailable_server status: {unavailable_session.status.value}")

            # Verify working server is connected or requires auth
            assert working_session.status in {
                SessionStatus.CONNECTED,
                SessionStatus.AUTH_PENDING
            }, f"Working server should be connected or require auth, got: {working_session.status}"

            # If auth is pending, complete it
            if working_session.status == SessionStatus.AUTH_PENDING:
                print("\n--- OAuth Required for working_server ---")
                challenges = await client.get_auth_challenges()
                print(f"  Found {len(challenges)} auth challenge(s)")

                # Wait for OAuth to complete and retry connection
                print("  Waiting for OAuth completion...")
                await asyncio.sleep(2)  # Give time for OAuth
                await client.connect(server="working_server")

                print(f"  working_server status after retry: {working_session.status.value}")

            # Verify working server is now connected
            assert working_session.is_operational, \
                f"Working server should be operational, status: {working_session.status}"
            print("✓ Working server is CONNECTED and operational")

            # Verify unavailable server has appropriate failure status
            assert unavailable_session.is_failed, \
                f"Unavailable server should be in failure state, got: {unavailable_session.status}"
            assert unavailable_session.status in {
                SessionStatus.SERVER_UNREACHABLE,
                SessionStatus.CONNECTION_FAILED,
                SessionStatus.FAILED
            }, f"Unavailable server should have connection failure status, got: {unavailable_session.status}"
            print(f"✓ Unavailable server correctly marked as {unavailable_session.status.value}")

            # Verify unavailable server can be retried
            assert unavailable_session.can_retry or unavailable_session.is_failed, \
                "Unavailable server should be in retryable or failed state"
            print("✓ Unavailable server status indicates it can be retried")

            # Phase 3: Use working server despite unavailable server
            print("\n--- Phase 3: Using working server ---")

            # List tools from working server
            tools = await client.list_tools(server_name="working_server")
            assert len(tools) > 0, "Working server should have tools available"
            print(f"✓ Got {len(tools)} tool(s) from working server:")
            for tool_info in tools:
                print(f"    - {tool_info.tool.name}: {tool_info.tool.description}")

            # Call a tool on the working server
            print("\n  Testing tool call on working server...")
            result = await client.call_tool(
                server_name="working_server",
                tool_name="echo",
                arguments={"message": "Test message from context manager test"}
            )

            assert result is not None, "Tool call should return result"
            print(f"✓ Tool call succeeded: {result.content[0].text if result.content else 'No content'}")

            # Phase 4: Verify error handling for unavailable server
            print("\n--- Phase 4: Verify unavailable server behavior ---")

            try:
                await client.call_tool(
                    server_name="unavailable_server",
                    tool_name="echo",
                    arguments={"message": "This should fail"}
                )
                pytest.fail("Should not be able to call tools on unavailable server")
            except ToolNotFoundException as e:
                print(f"✓ Unavailable server correctly raises ToolNotFoundException: {e}")

            # Phase 5: Summary
            print(f"\n{'='*70}")
            print("✅ MIXED SERVER AVAILABILITY TEST PASSED (Context Manager)")
            print(f"{'='*70}")
            print("Summary:")
            print("  - Client created with 2 servers using async with")
            print(f"  - working_server: {working_session.status.value}")
            print(f"  - unavailable_server: {unavailable_session.status.value}")
            print("  - Client successfully used working server")
            print("  - Client gracefully handled unavailable server")
            print("  - No exceptions thrown during connection attempts")
            print("  - Each session maintained independent status")
            print("  - Automatic cleanup via context manager")
            print(f"{'='*70}\n")

        # Context manager handles disconnect automatically
        # Still need to shutdown coordinator manually
        await coordinator.shutdown()
        print("✓ Coordinator shutdown complete")


@skip_manual
class TestMultipleUnavailableServers:
    """
    Test client behavior when all servers are unavailable.

    This tests the edge case where no servers are reachable at all.
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_all_servers_unavailable(
        self,
        storage_backend: InMemoryBackend
    ):
        """
        Test client gracefully handles scenario where all servers are unavailable.

        Verifies:
        - Client doesn't crash when no servers are available
        - All sessions set appropriate failure statuses
        - Client can still be instantiated and queried
        """
        print(f"\n{'='*70}")
        print("Testing All Servers Unavailable")
        print(f"{'='*70}\n")

        # Configure multiple unavailable servers
        servers = {
            "unavailable_1": {
                "url": "http://localhost:9991/mcp",
                "transport": "http",
                "auth": {"type": "oauth"},
            },
            "unavailable_2": {
                "url": "http://localhost:9992/mcp",
                "transport": "http",
                "auth": {"type": "oauth"},
            },
            "unavailable_3": {
                "url": "http://localhost:9993/mcp",
                "transport": "http",
                "auth": {"type": "oauth"},
            }
        }

        coordinator = LocalAuthCoordinator(
            backend=storage_backend,
            host="localhost",
            port=0,
            callback_path="/oauth/callback",
        )

        async with Client(
            servers,
            auth_coordinator=coordinator,
            storage_backend=storage_backend
        ) as client:
            print("--- Connection attempts completed (no exceptions thrown) ---")

            # Verify all sessions are in failure states
            print("\n--- Checking session statuses ---")
            for server_name, session in client.sessions.items():
                print(f"  {server_name}: {session.status.value}")
                assert session.is_failed, \
                    f"Server {server_name} should be in failure state"
                assert not session.is_operational, \
                    f"Server {server_name} should not be operational"

            print("✓ All sessions correctly marked as failed")

            # Verify client can still be queried
            print("\n--- Testing client queries ---")

            # List tools should return empty (no operational servers)
            tools = await client.list_tools()
            assert len(tools) == 0, "Should have no tools when all servers unavailable"
            print("✓ Client returns empty tool list (expected)")

            # Check auth challenges
            challenges = await client.get_auth_challenges()
            print(f"  Found {len(challenges)} auth challenges (expected: 0)")
            # Since servers are unreachable, we shouldn't get auth challenges
            assert len(challenges) == 0, "Unreachable servers shouldn't have auth challenges"
            print("✓ No auth challenges for unreachable servers")

            print(f"\n{'='*70}")
            print("✅ ALL SERVERS UNAVAILABLE TEST PASSED")
            print(f"{'='*70}")
            print("Summary:")
            print(f"  - Client created with {len(servers)} unavailable servers")
            print("  - All sessions marked as failed")
            print("  - No exceptions thrown during connection attempts")
            print("  - Client queries handled gracefully")
            print("  - Empty results returned as expected")
            print(f"{'='*70}\n")

        # Context manager handles disconnect automatically
        await coordinator.shutdown()


@skip_manual
class TestServerDisconnectionDetection:
    """
    Test client behavior when server disconnects during active session.

    This tests runtime failure scenarios where a server becomes unavailable
    after initial successful connection.
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(360)  # 6 minutes: 5 min OAuth wait + 1 min buffer
    async def test_detect_server_disconnection_during_operation(
        self,
        mcp_server: MCPServerFixture,
        storage_backend: InMemoryBackend
    ):
        """
        Test client detects when server disconnects during operation.

        Scenario:
        1. Connect to server successfully (with OAuth)
        2. Call a tool to verify it works
        3. Stop the MCP server (simulate crash/network failure)
        4. Attempt to call tool again
        5. Verify client detects disconnection and updates status
        6. Verify session status reflects the disconnection

        This simulates real-world scenarios like:
        - Server crashes
        - Network interruptions
        - Server restarts
        """
        print(f"\n{'='*70}")
        print("Testing Server Disconnection Detection")
        print(f"{'='*70}")
        print(f"MCP Server: {mcp_server.url}")
        print("This test will:")
        print("  1. Connect successfully")
        print("  2. Call a tool (success)")
        print("  3. Stop the server")
        print("  4. Try to call tool again (should detect failure)")
        print("When browser opens, please complete the OAuth authorization.")
        print(f"{'='*70}\n")

        # Configure server
        servers = {
            "test_server": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {"type": "oauth"},
            }
        }

        coordinator = LocalAuthCoordinator(
            backend=storage_backend,
            host="localhost",
            port=0,
            callback_path="/oauth/callback",
        )

        async with Client(
            servers,
            auth_coordinator=coordinator,
            storage_backend=storage_backend
        ) as client:
            # Phase 1: Verify initial connection
            print("\n--- Phase 1: Initial connection ---")
            session = client.sessions["test_server"]

            print(f"  Initial status: {session.status.value}")

            # Handle OAuth if needed
            if session.status == SessionStatus.AUTH_PENDING:
                print("  OAuth required, waiting for completion...")
                await asyncio.sleep(2)
                await client.connect(server="test_server")

            assert session.is_operational, \
                f"Server should be operational, got: {session.status}"
            print(f"✓ Server connected: {session.status.value}")

            # Phase 2: Call tool to verify it works
            print("\n--- Phase 2: Test tool call (server running) ---")
            try:
                result = await client.call_tool(
                    server_name="test_server",
                    tool_name="echo",
                    arguments={"message": "First call - server is up"}
                )
                assert result is not None
                print(f"✓ Tool call succeeded: {result.content[0].text if result.content else 'No content'}")
                print(f"  Session status: {session.status.value}")
                assert session.is_operational, "Session should still be operational"
            except Exception as e:
                pytest.fail(f"Initial tool call failed unexpectedly: {e}")

            # Phase 3: Stop the server (simulate crash)
            print("\n--- Phase 3: Stopping MCP server (simulate crash) ---")
            print("  Stopping server process...")
            mcp_server.stop()
            print("✓ Server stopped")

            # Give a moment for the connection to be closed
            await asyncio.sleep(1)

            # Phase 4: Check connection health
            print("\n--- Phase 4: Verify connection health ---")
            print("  Running connection health check...")

            is_healthy = await session.check_connection_health()
            print(f"  Health check result: {'healthy' if is_healthy else 'unhealthy'}")

            # Health check MUST detect the disconnection
            assert not is_healthy, "Health check should detect disconnected server"
            print("✓ Health check correctly detected disconnection")

            # Phase 5: Check session status after health check
            print("\n--- Phase 5: Verify session status ---")
            print(f"  Session status: {session.status.value}")
            print(f"  is_operational: {session.is_operational}")
            print(f"  is_failed: {session.is_failed}")
            print(f"  can_retry: {session.can_retry}")
            print(f"  connected: {session.connected}")

            # Session MUST reflect the disconnection
            assert not session.is_operational, \
                f"Session should NOT be operational after connection failure, status: {session.status}"
            assert not session.connected, \
                "Session should NOT be connected after connection failure"
            assert session.is_failed, \
                f"Session should be in failed state, got: {session.status}"
            assert session.status in {
                SessionStatus.CONNECTION_FAILED,
                SessionStatus.SERVER_UNREACHABLE,
                SessionStatus.DISCONNECTED,
                SessionStatus.FAILED
            }, f"Expected connection failure status, got: {session.status}"

            print("✓ Session correctly reflects disconnected state")
            print(f"✓ Session status properly updated to: {session.status.value}")

            # Phase 6: Verify reconnection is possible
            print("\n--- Phase 6: Verify reconnection pattern ---")
            print("  In production, you would:")
            print("  1. Detect failure via check_connection_health()")
            print("  2. Wait for server to restart")
            print("  3. Call client.connect() to reconnect")
            print("  4. Resume operations")

            # Restart server for cleanup purposes
            print("\n--- Restarting server for cleanup ---")
            mcp_server.start()
            await asyncio.sleep(2)  # Give server time to start
            print("✓ Server restarted")

            # Try to reconnect
            await client.connect(server="test_server", force_reconnect=True)
            print(f"  Session status after reconnect: {session.status.value}")

            # Phase 7: Summary
            print(f"\n{'='*70}")
            print("✅ SERVER DISCONNECTION DETECTION TEST PASSED")
            print(f"{'='*70}")
            print("Summary:")
            print("  - Initial connection successful")
            print("  - Tool call succeeded before server stop")
            print("  - Server stopped (simulated crash)")
            print("  - Subsequent operations detected failure")
            print("  - Session status properly tracked state")
            print("  - Reconnection pattern demonstrated")
            print(f"{'='*70}\n")

        # Cleanup
        await coordinator.shutdown()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

