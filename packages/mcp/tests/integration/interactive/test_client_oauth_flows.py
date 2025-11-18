"""
Integration tests for MCP client authentication flows.

Tests the main use cases:
1. Local Auth Coordinator with CLI (stateful, browser-based)
2. Remote Auth Coordinator (stateful, web app)
3. Remote Auth Coordinator (stateless, serverless with InMemoryBackend)
4. SQLite Backend (true serverless with persistence)
5. Subscribe/Notify Pattern (metadata propagation, retry patterns, concurrent auth)

The SQLite serverless tests (TestSQLiteServerlessExecution) validate that:
- Each request creates a COMPLETELY NEW execution context
- All state is stored in SQLite (no in-memory state persists)
- OAuth tokens are reused across separate invocations
- Multiple users are properly isolated

The Subscribe/Notify Pattern tests (TestSubscriberNotifyPattern) validate:
- Metadata propagation through callback events (Slack bot pattern)
- Stateful retry pattern (registering and retrying operations after auth)
- Multiple concurrent auth flows with proper metadata isolation
- Real-world production usage patterns

These tests use a real MCP server fixture and require manual OAuth approval.

**These tests are SKIPPED by default** because they require manual interaction.

To run them:
    export KEYCARD_ZONE_URL="https://your-zone.keycard.cloud"
    export RUN_INTERACTIVE_TESTS=1

    # Run all tests
    uv run pytest packages/mcp/tests/integration/interactive/test_client_oauth_flows.py -v -s

    # Run only SQLite serverless tests
    uv run pytest packages/mcp/tests/integration/interactive/test_client_oauth_flows.py::TestSQLiteServerlessExecution -v -s

    # Run only Subscribe/Notify pattern tests
    uv run pytest packages/mcp/tests/integration/interactive/test_client_oauth_flows.py::TestSubscriberNotifyPattern -v -s

    # Run a specific subscribe/notify test
    uv run pytest packages/mcp/tests/integration/interactive/test_client_oauth_flows.py::TestSubscriberNotifyPattern::test_metadata_propagation_in_callback_event -v -s
"""

from __future__ import annotations

import asyncio
import os
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from keycardai.mcp.client import (
    Client,
    ClientManager,
    InMemoryBackend,
    LocalAuthCoordinator,
    SQLiteBackend,
    StarletteAuthCoordinator,
)
from keycardai.mcp.client.auth.events import CompletionEvent

if TYPE_CHECKING:
    from .conftest import MCPServerFixture

# Skip interactive tests by default unless explicitly enabled
# Set RUN_INTERACTIVE_TESTS=1 to run these tests
RUN_INTERACTIVE_TESTS = os.getenv("RUN_INTERACTIVE_TESTS", "0") == "1"
skip_manual = pytest.mark.skipif(
    not RUN_INTERACTIVE_TESTS,
    reason="Interactive integration tests disabled by default. Set RUN_INTERACTIVE_TESTS=1 to run them."
)

# Note: Shared fixtures (mcp_server, callback_server, etc.) are defined in conftest.py


# ===== Helper Classes =====


class AuthCompletionSubscriber:
    """
    Subscriber that notifies when OAuth completion is handled.

    Used to wait for OAuth completion without polling storage.
    """

    def __init__(self):
        self.event = asyncio.Event()
        self.completion_event: CompletionEvent | None = None

    async def on_completion_handled(self, event: CompletionEvent) -> None:
        """Called when authentication completion has been handled."""
        self.completion_event = event
        self.event.set()

    async def wait_for_completion(self, timeout: float = 10) -> bool:
        """
        Wait for completion to be handled.

        Args:
            timeout: Maximum time to wait in seconds (default: 10)

        Returns:
            True if completion was handled, False if timeout
        """
        try:
            await asyncio.wait_for(self.event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False


# ===== Helper Functions =====


async def simulate_serverless_request(
    servers: dict,
    user_id: str,
    db_path: Path,
    callback_port: int,
    action: str,
    **kwargs
):
    """
    Simulate a single serverless function execution.

    This function:
    1. Creates a NEW client manager with SQLite backend
    2. Performs the requested action
    3. Completely tears down (simulating Lambda termination)

    Args:
        servers: MCP server configuration
        user_id: User context ID
        db_path: Path to SQLite database (persists across invocations)
        callback_port: Port for OAuth callback server
        action: Action to perform ("connect", "call_tool", "check_auth")
        **kwargs: Additional arguments for the action

    Returns:
        Result of the action
    """
    print(f"\n🚀 Serverless Execution START (action: {action})")

    # Create NEW storage backend (like a fresh Lambda execution)
    storage_backend = SQLiteBackend(db_path)

    # Create NEW coordinator (like a fresh Lambda execution)
    coordinator = StarletteAuthCoordinator(
        redirect_uri=f"http://localhost:{callback_port}/callback",
        backend=storage_backend
    )

    # Create NEW client manager (like a fresh Lambda execution)
    client_manager = ClientManager(
        servers,
        auth_coordinator=coordinator,
        storage_backend=storage_backend
    )

    result = None

    try:
        # Get client for user
        client = await client_manager.get_client(user_id)

        # Perform requested action
        if action == "connect":
            await client.connect()
            result = {"status": "connected"}

        elif action == "check_auth":
            await client.connect()
            pending_auth = await client.get_auth_challenges()
            result = {
                "has_pending_auth": len(pending_auth) > 0,
                "pending_auth": pending_auth
            }

        elif action == "call_tool":
            await client.connect()
            tool_name = kwargs.get("tool_name")
            tool_args = kwargs.get("tool_args", {})
            tool_result = await client.call_tool(tool_name, tool_args)
            result = {
                "status": "success",
                "result": tool_result
            }

        else:
            raise ValueError(f"Unknown action: {action}")

    finally:
        # CRITICAL: Complete teardown (simulating Lambda termination)
        # Clean up clients
        for client in client_manager.clients.values():
            await client.disconnect()
        await storage_backend.close()

        print(f"💀 Serverless Execution END (action: {action})")
        print("   - All connections closed")
        print("   - All memory freed")
        print("   - Only SQLite database persists\n")

    return result


# ===== Test Cases =====


@skip_manual
class TestLocalAuthCoordinatorIntegration:
    """
    Integration tests for Local Auth Coordinator with CLI usage.

    Use case: Direct Client usage with LocalAuthCoordinator
    - Stateful OAuth with browser redirect
    - Local callback server
    - Blocking wait for user consent

    To run this test:
    1. Set KEYCARD_ZONE_URL environment variable
    2. Run: uv run pytest packages/mcp/tests/integration/test_client_oauth_flows.py::TestLocalAuthCoordinatorIntegration -v
    3. Browser will open automatically for OAuth approval
    4. Complete the OAuth flow
    5. Test will verify the auth completed successfully
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(360)  # 6 minutes: 5 min OAuth wait + 1 min buffer
    async def test_local_auth_cli_flow(
        self,
        mcp_server: MCPServerFixture,
        storage_backend: InMemoryBackend
    ):
        """Test CLI flow with local auth coordinator and real MCP server."""
        print(f"\n{'='*70}")
        print(f"MCP Server running at: {mcp_server.url}")
        print("When the browser opens, please complete the OAuth authorization.")
        print(f"{'='*70}\n")

        # Configure servers
        servers = {
            "test": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {
                    "type": "oauth"
                }
            }
        }

        # Create coordinator with local callback server
        coordinator = LocalAuthCoordinator(
            backend=storage_backend,
            host="localhost",
            port=0,  # Auto-assign port
            callback_path="/oauth/callback"
        )

        # Create and use client
        # The LocalAuthCoordinator will block until auth completes
        async with Client(
            servers,
            auth_coordinator=coordinator,
            storage_backend=storage_backend
        ) as client:
            # By the time we get here, auth should be complete
            # because LocalAuthCoordinator blocks in handle_redirect()

            print("✓ Auth completed successfully!")

            # Verify no pending auth challenges
            pending_auth = await client.get_auth_challenges()
            assert len(pending_auth) == 0, "Expected no pending auth challenges"

            print("✓ No pending auth challenges")

            # Try to call a tool to verify we're authenticated
            result = await client.call_tool("echo", {"message": "Hello from test!"})
            print(f"✓ Tool call successful: {result}")

            # Verify the result content
            assert result is not None
            assert hasattr(result, 'content'), "Result should have content field"
            assert len(result.content) > 0, "Result should have content"
            # The echo tool returns "Echo: {message}"
            text_content = result.content[0].text if hasattr(result.content[0], 'text') else str(result.content[0])
            assert "Hello from test!" in text_content, f"Expected echo message in result, got: {text_content}"
            print(f"✓ Tool returned expected content: {text_content}")


@skip_manual
class TestStarletteAuthCoordinatorStateful:
    """
    Integration tests for Remote Auth Coordinator with stateful OAuth.

    Use case: Web app with ClientManager and stateful OAuth
    - Multiple users/contexts
    - Shared coordinator
    - Storage-based state management

    To run this test:
    1. Set KEYCARD_ZONE_URL environment variable
    2. Run: uv run pytest packages/mcp/tests/integration/test_client_oauth_flows.py::TestStarletteAuthCoordinatorStateful -v
    3. The test will print an authorization URL
    4. Open the URL in your browser and complete OAuth
    5. Test will poll and verify auth completed
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(180)  # 3 minutes timeout
    async def test_remote_auth_stateful_flow(
        self,
        mcp_server: MCPServerFixture,
        callback_server: tuple
    ):
        """Test web app flow with remote auth coordinator (stateful)."""
        coordinator, server = callback_server
        storage_backend = coordinator._backend

        print(f"\n{'='*70}")
        print(f"MCP Server running at: {mcp_server.url}")
        print(f"Callback server running at: http://localhost:{server.port}/callback")
        print("This test simulates a web app with remote OAuth flow.")
        print(f"{'='*70}\n")

        # Configure servers
        servers = {
            "test": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {
                    "type": "oauth"
                }
            }
        }

        # Create client manager
        client_manager = ClientManager(
            servers,
            auth_coordinator=coordinator,
            storage_backend=storage_backend
        )

        try:
            # Get client for a user
            user_id = "test_user_123"
            client = await client_manager.get_client(user_id)

            # Connect (will trigger auth flow but won't block)
            await client.connect()

            # Check for auth challenges
            pending_auth = await client.get_auth_challenges()

            if pending_auth:
                auth_url = pending_auth[0].get('authorization_url')
                print(f"\n{'='*70}")
                print("AUTHORIZATION REQUIRED")
                print(f"{'='*70}")
                print("Please open this URL in your browser:")
                print(f"\n{auth_url}\n")
                print("Complete the OAuth flow in your browser.")
                print("The callback will be received by the test server.")
                print(f"{'='*70}\n")

                webbrowser.open(auth_url)

                # Poll to check if auth completed
                # The callback server will handle the OAuth callback
                max_wait = 120  # 2 minutes
                waited = 0
                while waited < max_wait:
                    await asyncio.sleep(2)
                    waited += 2

                    # Check if auth completed
                    pending_auth = await client.get_auth_challenges()
                    if not pending_auth:
                        break

                    if waited % 10 == 0:
                        print(f"Still waiting... ({waited}s elapsed)")

                if pending_auth:
                    pytest.fail(f"OAuth flow not completed after {max_wait}s")

                # Reconnect after auth
                await client.connect(force_reconnect=True)

            print("✓ Auth completed successfully!")

            # Verify no more auth challenges
            pending_auth = await client.get_auth_challenges()
            assert len(pending_auth) == 0

            print("✓ No pending auth challenges")

            # Verify client is in manager
            assert user_id in client_manager.clients
            print("✓ Client properly managed")

            # Try to call a tool
            result = await client.call_tool("echo", {"message": "Hello from remote!"})
            assert result is not None
            text_content = result.content[0].text if hasattr(result.content[0], 'text') else str(result.content[0])
            assert "Hello from remote!" in text_content, f"Expected echo message, got: {text_content}"
            print(f"✓ Tool call successful: {text_content}")

        finally:
            # Clean up clients
            for client in client_manager.clients.values():
                try:
                    await client.disconnect()
                except Exception as e:
                    print(f"Warning: Error during client disconnect: {e}")


@skip_manual
class TestStarletteAuthCoordinatorStateless:
    """
    Integration tests for Remote Auth Coordinator with stateless OAuth.

    Use case: Serverless/stateless environment
    - No in-memory state
    - All state in storage
    - Callback can be handled in different process

    To run this test:
    1. Set KEYCARD_ZONE_URL environment variable
    2. Run: uv run pytest packages/mcp/tests/integration/test_client_oauth_flows.py::TestStarletteAuthCoordinatorStateless -v
    3. Follow the same flow as the stateful test
    4. Test will verify stateless callback handling works
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(180)  # 3 minutes timeout
    async def test_remote_auth_stateless_flow(
        self,
        mcp_server: MCPServerFixture,
        callback_server: tuple
    ):
        """Test serverless flow with remote auth coordinator (stateless)."""
        coordinator1, server = callback_server
        storage_backend = coordinator1._backend

        print(f"\n{'='*70}")
        print(f"MCP Server running at: {mcp_server.url}")
        print(f"Callback server running at: http://localhost:{server.port}/callback")
        print("This test simulates a serverless environment with stateless OAuth.")
        print(f"{'='*70}\n")

        # Configure servers with stateless OAuth
        servers = {
            "test": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {
                    "type": "oauth"
                }
            }
        }

        client_manager1 = ClientManager(
            servers,
            auth_coordinator=coordinator1,
            storage_backend=storage_backend
        )

        try:
            user_id = "test_user_456"
            client1 = await client_manager1.get_client(user_id)

            # Connect (triggers auth)
            await client1.connect()

            # Check for auth challenges
            pending_auth = await client1.get_auth_challenges()

            assert len(pending_auth) > 0, "Expected auth challenge for stateless flow"
            auth_url = pending_auth[0].get('authorization_url')

            print(f"\n{'='*70}")
            print("STATELESS OAUTH FLOW")
            print(f"{'='*70}")
            print("Process 1: Auth challenge created")
            print("Please open this URL in your browser:")
            print(f"\n{auth_url}\n")
            print("Waiting for OAuth to complete...")
            print(f"{'='*70}\n")

            webbrowser.open(auth_url)

            # Wait for user to complete OAuth
            max_wait = 120
            waited = 0
            while waited < max_wait:
                await asyncio.sleep(2)
                waited += 2

                # In a real serverless scenario, process 2 would handle the callback
                # Here we check if the stateless callback was processed
                # by creating a NEW coordinator with the SAME storage
                coordinator2 = StarletteAuthCoordinator(
                    redirect_uri="http://localhost:8080/callback",
                    backend=storage_backend  # Same storage!
                )

                # Check if tokens were stored
                temp_context = coordinator2.create_context(user_id)
                oauth_storage = temp_context.storage.get_namespace("server:test").get_namespace("connection").get_namespace("oauth")
                tokens = await oauth_storage.get("tokens")

                if tokens:
                    print("✓ Stateless callback processed (tokens found in storage)")
                    break

                if waited % 10 == 0:
                    print(f"Still waiting... ({waited}s elapsed)")

            if not tokens:
                pytest.fail(f"OAuth flow not completed after {max_wait}s")

            # Process 3: New request with same user (should use stored tokens)
            print("\nProcess 3: Creating new client with stored tokens...")

            client_manager2 = ClientManager(
                servers,
                auth_coordinator=coordinator2,
                storage_backend=storage_backend
            )

            try:
                # Get client again - should reuse stored tokens
                client2 = await client_manager2.get_client(user_id)
                await client2.connect()

                # Should have no auth challenges now
                pending_auth2 = await client2.get_auth_challenges()
                assert len(pending_auth2) == 0, "Expected no auth challenges after token storage"

                print("✓ Stateless flow completed - tokens reused from storage")

                # Try to call a tool
                result = await client2.call_tool("echo", {"message": "Hello from stateless!"})
                assert result is not None
                text_content = result.content[0].text if hasattr(result.content[0], 'text') else str(result.content[0])
                assert "Hello from stateless!" in text_content, f"Expected echo message, got: {text_content}"
                print(f"✓ Tool call successful with reused tokens: {text_content}")

            finally:
                # Clean up clients
                for client in client_manager2.clients.values():
                    await client.disconnect()

        finally:
            # Clean up clients
            for client in client_manager1.clients.values():
                await client.disconnect()


@skip_manual
class TestMultiUserScenarios:
    """
    Integration tests for multi-user scenarios.

    Tests that multiple users can authenticate independently
    with proper isolation.
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(240)  # 4 minutes timeout (2 users)
    async def test_multiple_users_with_remote_coordinator(
        self,
        mcp_server: MCPServerFixture,
        callback_server: tuple
    ):
        """Test multiple users authenticating through same coordinator."""
        coordinator, server = callback_server
        storage_backend = coordinator._backend

        print(f"\n{'='*70}")
        print(f"MCP Server running at: {mcp_server.url}")
        print(f"Callback server running at: http://localhost:{server.port}/callback")
        print("This test verifies multi-user isolation.")
        print("You'll need to complete OAuth twice (once per user).")
        print(f"{'='*70}\n")

        servers = {
            "test": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {
                    "type": "oauth"
                }
            }
        }

        client_manager = ClientManager(
            servers,
            auth_coordinator=coordinator,
            storage_backend=storage_backend
        )

        try:
            # User 1
            print("\n--- Authenticating User 1 ---")
            client1 = await client_manager.get_client("user_1")
            await client1.connect()

            pending_auth1 = await client1.get_auth_challenges()
            if pending_auth1:
                print(f"User 1 auth URL:\n{pending_auth1[0]['authorization_url']}\n")
                print("Please complete OAuth for User 1...")
                webbrowser.open(pending_auth1[0]['authorization_url'])

                # Wait for completion
                max_wait = 60
                for _ in range(max_wait // 2):
                    await asyncio.sleep(2)
                    pending_auth1 = await client1.get_auth_challenges()
                    if not pending_auth1:
                        break

                if pending_auth1:
                    pytest.skip("User 1 OAuth not completed in time")

                await client1.connect(force_reconnect=True)

            print("✓ User 1 authenticated")

            # User 2
            print("\n--- Authenticating User 2 ---")
            client2 = await client_manager.get_client("user_2")
            await client2.connect()

            pending_auth2 = await client2.get_auth_challenges()
            if pending_auth2:
                print(f"User 2 auth URL:\n{pending_auth2[0]['authorization_url']}\n")
                print("Please complete OAuth for User 2...")
                webbrowser.open(pending_auth2[0]['authorization_url'])

                # Wait for completion
                for _ in range(max_wait // 2):
                    await asyncio.sleep(2)
                    pending_auth2 = await client2.get_auth_challenges()
                    if not pending_auth2:
                        break

                if pending_auth2:
                    pytest.skip("User 2 OAuth not completed in time")

                await client2.connect(force_reconnect=True)

            print("✓ User 2 authenticated")

            # Verify both users are managed independently
            assert "user_1" in client_manager.clients
            assert "user_2" in client_manager.clients
            assert client_manager.clients["user_1"] is not client_manager.clients["user_2"]

            print("✓ Both users properly isolated")

            # Both can call tools independently
            result1 = await client1.call_tool("echo", {"message": "User 1"})
            result2 = await client2.call_tool("echo", {"message": "User 2"})

            # Verify results
            assert result1 is not None and result2 is not None
            text1 = result1.content[0].text if hasattr(result1.content[0], 'text') else str(result1.content[0])
            text2 = result2.content[0].text if hasattr(result2.content[0], 'text') else str(result2.content[0])
            assert "User 1" in text1, f"Expected User 1 echo, got: {text1}"
            assert "User 2" in text2, f"Expected User 2 echo, got: {text2}"

            print(f"✓ User 1 result: {text1}")
            print(f"✓ User 2 result: {text2}")

        finally:
            # Clean up clients
            for client in client_manager.clients.values():
                try:
                    await client.disconnect()
                except Exception as e:
                    print(f"Warning: Error during client disconnect: {e}")


@skip_manual
class TestSQLiteServerlessExecution:
    """
    Integration tests for SQLite backend in true serverless execution.

    These tests simulate AWS Lambda / Google Cloud Functions / Azure Functions where:
    - Each request is a completely new execution context
    - No memory state persists between requests
    - Only the SQLite database persists

    This validates the serverless architecture works correctly.
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)  # 1 minute timeout
    async def test_serverless_oauth_flow_with_sqlite_persistence(
        self,
        mcp_server: MCPServerFixture,
        callback_server_sqlite: tuple,
        temp_db_path: Path
    ):
        """
        Test complete serverless OAuth flow with SQLite persistence.

        Simulates:
        1. Request 1: User tries to call tool, gets OAuth challenge
        2. User completes OAuth in browser (callback handled by persistent server)
        3. Request 2: User calls tool again, uses stored tokens (no re-auth)
        4. Request 3: Another tool call, verifies tokens still work

        Each request is a COMPLETELY NEW execution context.
        """
        coordinator, server = callback_server_sqlite

        print(f"\n{'='*70}")
        print("SERVERLESS OAUTH FLOW TEST")
        print(f"{'='*70}")
        print(f"MCP Server: {mcp_server.url}")
        print(f"Callback Server: http://localhost:{server.port}/callback")
        print(f"SQLite Database: {temp_db_path}")
        print(f"{'='*70}\n")

        servers = {
            "test": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {
                    "type": "oauth"
                }
            }
        }

        user_id = "serverless_user_123"

        # ===== REQUEST 1: Initial connection attempt (will need OAuth) =====
        print("📡 REQUEST 1: Initial connection (expecting OAuth challenge)")

        result1 = await simulate_serverless_request(
            servers=servers,
            user_id=user_id,
            db_path=temp_db_path,
            callback_port=server.port,
            action="check_auth"
        )

        assert result1["has_pending_auth"], "Expected OAuth challenge on first request"
        auth_url = result1["pending_auth"][0]["authorization_url"]

        print("✓ OAuth challenge received")
        print(f"\n{'='*70}")
        print("PLEASE COMPLETE OAUTH")
        print(f"{'='*70}")
        print("Open this URL in your browser:")
        print(f"\n{auth_url}\n")
        print("The callback will be handled by the persistent callback server.")
        print(f"{'='*70}\n")

        # Subscribe to the coordinator to get notified when callback is handled
        subscriber = AuthCompletionSubscriber()
        coordinator.subscribe(subscriber)


        webbrowser.open(auth_url)

        # Wait for OAuth to complete using subscriber pattern
        # The callback server (which stays running) will handle the callback
        # and store tokens in the SQLite database
        print("⏳ Waiting for OAuth completion...")

        # Wait for callback (with timeout)
        max_wait = 10  # 10 seconds (auth happens quickly with webbrowser)
        callback_received = await subscriber.wait_for_completion(timeout=max_wait)

        if not callback_received:
            pytest.fail(f"OAuth not completed after {max_wait}s")

        if subscriber.completion_event and not subscriber.completion_event.success:
            pytest.fail(f"OAuth callback failed: {subscriber.completion_event.error}")

        print("✓ OAuth completed - tokens stored in SQLite\n")

        # ===== REQUEST 2: Try tool call with stored tokens =====
        print("📡 REQUEST 2: Call tool (should use stored tokens)")

        result2 = await simulate_serverless_request(
            servers=servers,
            user_id=user_id,
            db_path=temp_db_path,
            callback_port=server.port,
            action="call_tool",
            tool_name="echo",
            tool_args={"message": "Hello from serverless!"}
        )

        assert result2["status"] == "success", "Tool call should succeed"
        tool_result = result2["result"]
        assert tool_result is not None, "Should get tool result"

        text_content = tool_result.content[0].text if hasattr(tool_result.content[0], 'text') else str(tool_result.content[0])
        assert "Hello from serverless!" in text_content, f"Expected echo message, got: {text_content}"

        print(f"✓ Tool call successful: {text_content}")
        print("✓ Tokens were reused from SQLite (no re-auth needed)\n")

        # ===== REQUEST 3: Another tool call to verify persistence =====
        print("📡 REQUEST 3: Another tool call (verify tokens still work)")

        result3 = await simulate_serverless_request(
            servers=servers,
            user_id=user_id,
            db_path=temp_db_path,
            callback_port=server.port,
            action="call_tool",
            tool_name="get_user_info",
            tool_args={}
        )

        assert result3["status"] == "success", "Second tool call should succeed"
        print("✓ Second tool call successful")
        print("✓ Tokens persisted across multiple serverless executions\n")

        # ===== REQUEST 4: Verify no auth challenges =====
        print("📡 REQUEST 4: Check auth status (should be clean)")

        result4 = await simulate_serverless_request(
            servers=servers,
            user_id=user_id,
            db_path=temp_db_path,
            callback_port=server.port,
            action="check_auth"
        )

        assert not result4["has_pending_auth"], "Should have no pending auth challenges"
        print("✓ No pending auth challenges\n")

        print(f"{'='*70}")
        print("✅ SERVERLESS TEST PASSED")
        print(f"{'='*70}")
        print("Summary:")
        print("  - OAuth completed in Request 1")
        print("  - Tokens stored in SQLite")
        print("  - Requests 2-4 reused tokens from SQLite")
        print("  - Each request was a completely new execution context")
        print("  - No in-memory state persisted between requests")
        print(f"{'='*70}\n")

    @pytest.mark.asyncio
    @pytest.mark.timeout(90)  # 1.5 minutes timeout
    async def test_serverless_multi_user_isolation(
        self,
        mcp_server: MCPServerFixture,
        callback_server_sqlite: tuple,
        temp_db_path: Path
    ):
        """
        Test that multiple users are properly isolated in serverless execution.

        Simulates:
        1. User A authenticates and calls a tool
        2. User B authenticates and calls a tool
        3. Both users' tokens are stored separately in SQLite
        4. Both users can make subsequent requests without re-auth
        5. Users' data is properly isolated
        """
        coordinator, server = callback_server_sqlite

        print(f"\n{'='*70}")
        print("SERVERLESS MULTI-USER ISOLATION TEST")
        print(f"{'='*70}")
        print(f"SQLite Database: {temp_db_path}")
        print("Testing that multiple users are properly isolated")
        print(f"{'='*70}\n")

        servers = {
            "test": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {
                    "type": "oauth"
                }
            }
        }

        # ===== USER A AUTHENTICATION =====
        print("👤 USER A: Initial authentication")
        user_a_id = "user_a"

        result_a1 = await simulate_serverless_request(
            servers=servers,
            user_id=user_a_id,
            db_path=temp_db_path,
            callback_port=server.port,
            action="check_auth"
        )

        if result_a1["has_pending_auth"]:
            auth_url_a = result_a1["pending_auth"][0]["authorization_url"]
            print(f"User A auth URL:\n{auth_url_a}\n")
            print("Please complete OAuth for User A...")

            # Subscribe to coordinator for User A
            subscriber_a = AuthCompletionSubscriber()
            coordinator.subscribe(subscriber_a)

            webbrowser.open(auth_url_a)

            # Wait for completion
            max_wait = 10  # 10 seconds (auth happens quickly with webbrowser)
            callback_received = await subscriber_a.wait_for_completion(timeout=max_wait)

            if not callback_received:
                pytest.fail("User A OAuth not completed in time")

            if subscriber_a.completion_event and not subscriber_a.completion_event.success:
                pytest.fail(f"User A OAuth failed: {subscriber_a.completion_event.error}")

            print("✓ User A authenticated\n")

        # ===== USER B AUTHENTICATION =====
        print("👤 USER B: Initial authentication")
        user_b_id = "user_b"

        result_b1 = await simulate_serverless_request(
            servers=servers,
            user_id=user_b_id,
            db_path=temp_db_path,
            callback_port=server.port,
            action="check_auth"
        )

        if result_b1["has_pending_auth"]:
            auth_url_b = result_b1["pending_auth"][0]["authorization_url"]
            print(f"User B auth URL:\n{auth_url_b}\n")
            print("Please complete OAuth for User B...")

            # Subscribe to coordinator for User B
            subscriber_b = AuthCompletionSubscriber()
            coordinator.subscribe(subscriber_b)

            webbrowser.open(auth_url_b)

            # Wait for completion
            callback_received = await subscriber_b.wait_for_completion(timeout=max_wait)

            if not callback_received:
                pytest.fail("User B OAuth not completed in time")

            if subscriber_b.completion_event and not subscriber_b.completion_event.success:
                pytest.fail(f"User B OAuth failed: {subscriber_b.completion_event.error}")

            print("✓ User B authenticated\n")

        # ===== VERIFY ISOLATION =====
        print("🔒 Verifying user isolation in SQLite")

        # Check that both users have separate data
        check_backend = SQLiteBackend(temp_db_path)
        try:
            keys_a = await check_backend.list_keys(prefix=f"client:{user_a_id}:")
            keys_b = await check_backend.list_keys(prefix=f"client:{user_b_id}:")

            assert len(keys_a) > 0, "User A should have stored data"
            assert len(keys_b) > 0, "User B should have stored data"

            # Verify no overlap
            keys_a_set = set(keys_a)
            keys_b_set = set(keys_b)
            overlap = keys_a_set & keys_b_set
            assert len(overlap) == 0, f"Users should not share keys, but found overlap: {overlap}"

            print(f"✓ User A has {len(keys_a)} keys")
            print(f"✓ User B has {len(keys_b)} keys")
            print("✓ No key overlap - users are properly isolated\n")

        finally:
            await check_backend.close()

        # ===== BOTH USERS MAKE TOOL CALLS =====
        print("🔧 Both users calling tools with stored tokens")

        result_a2 = await simulate_serverless_request(
            servers=servers,
            user_id=user_a_id,
            db_path=temp_db_path,
            callback_port=server.port,
            action="call_tool",
            tool_name="echo",
            tool_args={"message": "User A message"}
        )

        result_b2 = await simulate_serverless_request(
            servers=servers,
            user_id=user_b_id,
            db_path=temp_db_path,
            callback_port=server.port,
            action="call_tool",
            tool_name="echo",
            tool_args={"message": "User B message"}
        )

        assert result_a2["status"] == "success", "User A tool call should succeed"
        assert result_b2["status"] == "success", "User B tool call should succeed"

        print("✓ User A tool call successful")
        print("✓ User B tool call successful")
        print("✓ Both users can use stored tokens independently\n")

        print(f"{'='*70}")
        print("✅ MULTI-USER ISOLATION TEST PASSED")
        print(f"{'='*70}")
        print("Summary:")
        print("  - Both users authenticated separately")
        print("  - Tokens stored with proper user namespacing")
        print("  - No data leakage between users")
        print("  - Both users can make requests independently")
        print(f"{'='*70}\n")


@skip_manual
class TestSubscriberNotifyPattern:
    """
    Integration tests for the subscribe/notify pattern with metadata and retries.

    These tests validate the real-world usage patterns demonstrated in the
    Slack bot example, including:
    - Metadata propagation through callback events
    - Stateful retry pattern (registering pending operations and retrying after auth)
    - Multiple concurrent auth flows with different metadata

    This ensures the subscriber/notify system works correctly for production use cases.
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(90)  # 1.5 minutes timeout
    async def test_metadata_propagation_in_callback_event(
        self,
        mcp_server: MCPServerFixture,
        callback_server_sqlite: tuple,
        temp_db_path: Path
    ):
        """
        Test that metadata passed during client creation is available in completion event.

        This validates the pattern used in the Slack bot where user context
        (user_id, thread_id, channel_id) is passed as metadata and retrieved
        in the completion subscriber.
        """
        coordinator, server = callback_server_sqlite

        print(f"\n{'='*70}")
        print("METADATA PROPAGATION TEST")
        print(f"{'='*70}")
        print("Testing that metadata flows through auth completion events")
        print(f"{'='*70}\n")

        servers = {
            "test": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {
                    "type": "oauth"
                }
            }
        }

        # Define metadata to pass through
        test_metadata = {
            "user_id": "user_12345",
            "thread_id": "1234567890.123456",
            "channel_id": "C01234567",
            "custom_field": "test_value"
        }

        # Create a subscriber that captures the completion event
        class MetadataCapturingSubscriber:
            def __init__(self):
                self.event = asyncio.Event()
                self.completion_event: CompletionEvent | None = None

            async def on_completion_handled(self, event: CompletionEvent) -> None:
                """Capture the completion event."""
                self.completion_event = event
                self.event.set()

            async def wait_for_completion(self, timeout: float = 10) -> bool:
                """Wait for completion to be handled."""
                try:
                    await asyncio.wait_for(self.event.wait(), timeout=timeout)
                    return True
                except asyncio.TimeoutError:
                    return False

        subscriber = MetadataCapturingSubscriber()
        coordinator.subscribe(subscriber)

        # Create storage backend (we manage its lifecycle)
        storage_backend = SQLiteBackend(temp_db_path)

        # Create a client manager and get client with metadata
        client_manager = ClientManager(
            servers,
            auth_coordinator=coordinator,
            storage_backend=storage_backend
        )

        try:
            # Get client with metadata
            user_id = "test_user_metadata"
            client = await client_manager.get_client(user_id, metadata=test_metadata)

            # Connect to trigger auth
            await client.connect()

            # Check for auth challenges
            pending_auth = await client.get_auth_challenges()

            if not pending_auth:
                pytest.skip("No auth challenge generated (already authenticated)")

            auth_url = pending_auth[0].get('authorization_url')

            print(f"Authorization URL:\n{auth_url}\n")
            print("Please complete OAuth in your browser...")

            webbrowser.open(auth_url)

            # Wait for completion
            max_wait = 10
            callback_received = await subscriber.wait_for_completion(timeout=max_wait)

            if not callback_received:
                pytest.fail(f"OAuth not completed after {max_wait}s")

            # Verify completion event was captured
            assert subscriber.completion_event is not None, "Completion event should be captured"
            assert subscriber.completion_event.success, "Completion should succeed"

            print("✓ Completion event captured")

            # Verify metadata is present in the event
            event_metadata = subscriber.completion_event.metadata
            assert event_metadata is not None, "Event should have metadata"

            print(f"✓ Event metadata: {event_metadata}")

            # Verify all metadata fields are present and correct
            for key, expected_value in test_metadata.items():
                actual_value = event_metadata.get(key)
                assert actual_value == expected_value, \
                    f"Metadata field '{key}' should be '{expected_value}', got '{actual_value}'"
                print(f"✓ Metadata field '{key}' = '{actual_value}'")

            print("\n✓ All metadata propagated correctly")

        finally:
            # Clean up
            for client in client_manager.clients.values():
                await client.disconnect()
            await storage_backend.close()

            # Give background tasks (cleanup) time to complete before server shutdown
            await asyncio.sleep(0.5)

        print(f"\n{'='*70}")
        print("✅ METADATA PROPAGATION TEST PASSED")
        print(f"{'='*70}")
        print("Summary:")
        print("  - Client created with custom metadata")
        print("  - OAuth flow completed")
        print("  - Subscriber received completion event")
        print("  - All metadata fields propagated correctly")
        print(f"{'='*70}\n")

    @pytest.mark.asyncio
    @pytest.mark.timeout(90)  # 1.5 minutes timeout
    async def test_stateful_retry_pattern(
        self,
        mcp_server: MCPServerFixture,
        callback_server_sqlite: tuple,
        temp_db_path: Path
    ):
        """
        Test the pattern of registering pending operations and retrying after auth.

        This validates the Slack bot pattern where:
        1. User tries to call a tool but needs auth
        2. Auth URL is sent to user
        3. Pending operation is registered
        4. When auth completes, the subscriber retrieves pending operation
        5. Original operation is retried automatically
        """
        coordinator, server = callback_server_sqlite

        print(f"\n{'='*70}")
        print("STATEFUL RETRY PATTERN TEST")
        print(f"{'='*70}")
        print("Testing pending operation registration and retry after auth")
        print(f"{'='*70}\n")

        servers = {
            "test": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {
                    "type": "oauth"
                }
            }
        }

        # Simulate the Slack bot's AuthRetryHandler pattern
        class RetryHandler:
            """Handler that retries operations after auth completes."""

            def __init__(self):
                self.event = asyncio.Event()
                self.pending: dict[str, dict] = {}
                self.retry_called = False
                self.retry_result = None
                self.completion_event: CompletionEvent | None = None

            def register(self, context_id: str, operation_info: dict):
                """Register a pending operation."""
                self.pending[context_id] = operation_info
                print(f"  Registered pending operation for context: {context_id}")

            async def on_completion_handled(self, event: CompletionEvent) -> None:
                """Handle auth completion and retry operation."""
                self.completion_event = event

                if not event.success:
                    print(f"  Auth failed: {event.error}")
                    self.event.set()
                    return

                # Extract user context from metadata
                user_id = event.metadata.get("user_id")
                thread_id = event.metadata.get("thread_id")

                if not (user_id and thread_id):
                    print("  Missing user_id or thread_id in metadata")
                    self.event.set()
                    return

                context_id = f"{user_id}:{thread_id}"
                operation_info = self.pending.get(context_id)

                if not operation_info:
                    print(f"  No pending operation for context: {context_id}")
                    self.event.set()
                    return

                print(f"  Found pending operation: {operation_info['action']}")

                # Retry the operation
                try:
                    self.retry_result = await self._retry_operation(operation_info)
                    self.retry_called = True
                    print("  ✓ Retry successful")
                except Exception as e:
                    print(f"  ✗ Retry failed: {e}")
                finally:
                    del self.pending[context_id]
                    self.event.set()

            async def _retry_operation(self, operation_info: dict) -> any:
                """Actually retry the operation."""
                # In the real Slack bot, this would re-run the agent
                # Here we simulate by calling a tool directly
                return await operation_info["retry_func"]()

            async def wait_for_retry(self, timeout: float = 10) -> bool:
                """Wait for retry to complete."""
                try:
                    await asyncio.wait_for(self.event.wait(), timeout=timeout)
                    return True
                except asyncio.TimeoutError:
                    return False

        retry_handler = RetryHandler()
        coordinator.subscribe(retry_handler)

        # Test metadata
        test_metadata = {
            "user_id": "retry_user_001",
            "thread_id": "1234567890.123456",
            "channel_id": "C01234567",
        }

        # Create storage backend (we manage its lifecycle)
        storage_backend = SQLiteBackend(temp_db_path)

        # Create client manager
        client_manager = ClientManager(
            servers,
            auth_coordinator=coordinator,
            storage_backend=storage_backend
        )

        try:
            user_id = "test_user_retry"
            context_id = f"{test_metadata['user_id']}:{test_metadata['thread_id']}"

            print("Step 1: Create client and attempt connection")
            client = await client_manager.get_client(user_id, metadata=test_metadata)
            await client.connect()

            # Check for auth challenges
            pending_auth = await client.get_auth_challenges()

            if not pending_auth:
                pytest.skip("No auth challenge generated (already authenticated)")

            auth_url = pending_auth[0].get('authorization_url')
            print("✓ Auth challenge received")

            print("\nStep 2: Register pending operation (simulating tool call)")

            # Define a retry function that will be called after auth
            retry_called_flag = False

            async def retry_operation():
                nonlocal retry_called_flag
                # Simulate calling the tool after auth completes
                print("    Executing retry: calling echo tool...")
                retry_called_flag = True

                # Get a fresh client reference (auth should be complete now)
                retry_client = await client_manager.get_client(user_id, metadata=test_metadata)
                await retry_client.connect()
                result = await retry_client.call_tool("echo", {"message": "Retry successful!"})
                return result

            retry_handler.register(context_id, {
                "action": "call_tool",
                "tool_name": "echo",
                "args": {"message": "Retry successful!"},
                "retry_func": retry_operation
            })

            print("✓ Pending operation registered")

            print("\nStep 3: Complete OAuth (this should trigger retry)")
            print(f"Authorization URL:\n{auth_url}\n")
            print("Please complete OAuth in your browser...")

            webbrowser.open(auth_url)

            # Wait for retry to complete
            max_wait = 15  # Give more time for retry
            retry_completed = await retry_handler.wait_for_retry(timeout=max_wait)

            if not retry_completed:
                pytest.fail(f"Retry not completed after {max_wait}s")

            print("✓ Auth completion received")

            # Verify retry was called
            assert retry_handler.retry_called, "Retry should have been called"
            assert retry_handler.completion_event is not None, "Should have completion event"
            assert retry_handler.completion_event.success, "Completion should succeed"
            assert retry_called_flag, "Retry function should have been executed"

            print("✓ Retry handler executed")

            # Verify retry result
            assert retry_handler.retry_result is not None, "Retry should return result"
            text_content = retry_handler.retry_result.content[0].text if hasattr(
                retry_handler.retry_result.content[0], 'text'
            ) else str(retry_handler.retry_result.content[0])
            assert "Retry successful!" in text_content, f"Expected retry message, got: {text_content}"

            print(f"✓ Retry result: {text_content}")

            # Verify pending operation was cleaned up
            assert context_id not in retry_handler.pending, "Pending operation should be removed"
            print("✓ Pending operation cleaned up")

        finally:
            # Clean up
            for client in client_manager.clients.values():
                await client.disconnect()
            await storage_backend.close()

            # Give background tasks (cleanup) time to complete before server shutdown
            await asyncio.sleep(0.5)

        print(f"\n{'='*70}")
        print("✅ STATEFUL RETRY PATTERN TEST PASSED")
        print(f"{'='*70}")
        print("Summary:")
        print("  - Auth challenge triggered")
        print("  - Pending operation registered")
        print("  - OAuth completed")
        print("  - Subscriber received completion with metadata")
        print("  - Pending operation retrieved and retried")
        print("  - Original operation succeeded after auth")
        print(f"{'='*70}\n")

    @pytest.mark.asyncio
    @pytest.mark.timeout(180)  # 3 minutes timeout (multiple users)
    async def test_multiple_concurrent_auth_flows_with_metadata(
        self,
        mcp_server: MCPServerFixture,
        callback_server_sqlite: tuple,
        temp_db_path: Path
    ):
        """
        Test multiple users authenticating concurrently with different metadata.

        This validates that:
        1. Multiple auth flows can happen simultaneously
        2. Each subscriber receives only their own callback events
        3. Metadata is correctly isolated between users
        4. No cross-contamination between concurrent auth flows
        """
        coordinator, server = callback_server_sqlite

        print(f"\n{'='*70}")
        print("CONCURRENT AUTH FLOWS TEST")
        print(f"{'='*70}")
        print("Testing multiple users with different metadata authenticating concurrently")
        print(f"{'='*70}\n")

        servers = {
            "test": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {
                    "type": "oauth"
                }
            }
        }

        # Create a global subscriber that tracks all events
        class ConcurrentAuthTracker:
            """Tracks completions for multiple concurrent users."""

            def __init__(self):
                self.events: dict[str, CompletionEvent] = {}
                self.completion_events: dict[str, asyncio.Event] = {}

            def register_user(self, user_id: str):
                """Register a user to track."""
                self.completion_events[user_id] = asyncio.Event()

            async def on_completion_handled(self, event: CompletionEvent) -> None:
                """Handle completion for any user."""
                # Extract user from metadata
                metadata_user_id = event.metadata.get("user_id")

                if not metadata_user_id:
                    print("  Warning: Completion with no user_id in metadata")
                    return

                print(f"  Completion received for user: {metadata_user_id}")

                # Store event
                self.events[metadata_user_id] = event

                # Signal completion
                if metadata_user_id in self.completion_events:
                    self.completion_events[metadata_user_id].set()

            async def wait_for_user(self, user_id: str, timeout: float = 10) -> bool:
                """Wait for a specific user's completion."""
                if user_id not in self.completion_events:
                    return False

                try:
                    await asyncio.wait_for(
                        self.completion_events[user_id].wait(),
                        timeout=timeout
                    )
                    return True
                except asyncio.TimeoutError:
                    return False

        tracker = ConcurrentAuthTracker()
        coordinator.subscribe(tracker)

        # Define metadata for 3 different users
        users = [
            {
                "context_id": "concurrent_user_1",
                "metadata": {
                    "user_id": "slack_user_001",
                    "thread_id": "1234567890.111111",
                    "channel_id": "C0001",
                    "team_id": "T001"
                }
            },
            {
                "context_id": "concurrent_user_2",
                "metadata": {
                    "user_id": "slack_user_002",
                    "thread_id": "1234567890.222222",
                    "channel_id": "C0002",
                    "team_id": "T002"
                }
            },
            {
                "context_id": "concurrent_user_3",
                "metadata": {
                    "user_id": "slack_user_003",
                    "thread_id": "1234567890.333333",
                    "channel_id": "C0003",
                    "team_id": "T003"
                }
            }
        ]

        # Register users with tracker
        for user in users:
            tracker.register_user(user["metadata"]["user_id"])

        # Create storage backend (we manage its lifecycle)
        storage_backend = SQLiteBackend(temp_db_path)

        # Create client manager
        client_manager = ClientManager(
            servers,
            auth_coordinator=coordinator,
            storage_backend=storage_backend
        )

        auth_urls = []

        try:
            # Step 1: Create clients and trigger auth for all users
            print("Step 1: Creating clients and triggering auth for all users\n")

            for i, user in enumerate(users, 1):
                print(f"  User {i}: {user['metadata']['user_id']}")

                client = await client_manager.get_client(
                    user["context_id"],
                    metadata=user["metadata"]
                )
                await client.connect()

                pending_auth = await client.get_auth_challenges()

                if not pending_auth:
                    print("    Skipping (already authenticated)")
                    continue

                auth_url = pending_auth[0].get('authorization_url')
                auth_urls.append({
                    "user": user,
                    "url": auth_url
                })
                print("    ✓ Auth URL generated")

            if not auth_urls:
                pytest.skip("All users already authenticated")

            print(f"\n✓ Generated {len(auth_urls)} auth URLs")

            # Step 2: Complete auth for each user
            print("\nStep 2: Completing OAuth for each user\n")

            for i, auth_info in enumerate(auth_urls, 1):
                user = auth_info["user"]
                url = auth_info["url"]
                metadata_user_id = user["metadata"]["user_id"]

                print(f"  User {i}: {metadata_user_id}")
                print(f"  URL: {url}")
                print("  Please complete OAuth in your browser...")

                webbrowser.open(url)

                # Wait for this user's callback
                max_wait = 15
                completed = await tracker.wait_for_user(metadata_user_id, timeout=max_wait)

                if not completed:
                    pytest.fail(f"OAuth for {metadata_user_id} not completed after {max_wait}s")

                print("  ✓ OAuth completed\n")

            print(f"✓ All {len(auth_urls)} users completed OAuth")

            # Step 3: Verify each user received correct metadata
            print("\nStep 3: Verifying metadata isolation\n")

            for user in users:
                metadata_user_id = user["metadata"]["user_id"]

                # Skip if user didn't need auth
                if metadata_user_id not in tracker.events:
                    print(f"  Skipping {metadata_user_id} (no auth needed)")
                    continue

                event = tracker.events[metadata_user_id]

                # Verify success
                assert event.success, f"Completion for {metadata_user_id} should succeed"

                # Verify metadata matches
                expected_metadata = user["metadata"]
                actual_metadata = event.metadata

                print(f"  User: {metadata_user_id}")
                for key, expected_value in expected_metadata.items():
                    actual_value = actual_metadata.get(key)
                    assert actual_value == expected_value, \
                        f"User {metadata_user_id}: metadata '{key}' should be '{expected_value}', got '{actual_value}'"
                    print(f"    ✓ {key} = {actual_value}")

                # Verify no cross-contamination with other users' metadata
                for other_user in users:
                    if other_user["metadata"]["user_id"] == metadata_user_id:
                        continue

                    # Check that this user's metadata doesn't contain other user's unique values
                    other_thread_id = other_user["metadata"]["thread_id"]
                    other_channel_id = other_user["metadata"]["channel_id"]

                    assert actual_metadata.get("thread_id") != other_thread_id, \
                        f"User {metadata_user_id} should not have thread_id from other user"
                    assert actual_metadata.get("channel_id") != other_channel_id, \
                        f"User {metadata_user_id} should not have channel_id from other user"

                print(f"  ✓ No cross-contamination for {metadata_user_id}\n")

            print("✓ All metadata correctly isolated")

        finally:
            # Clean up
            for client in client_manager.clients.values():
                await client.disconnect()
            await storage_backend.close()

            # Give background tasks (cleanup) time to complete before server shutdown
            await asyncio.sleep(0.5)

        print(f"\n{'='*70}")
        print("✅ CONCURRENT AUTH FLOWS TEST PASSED")
        print(f"{'='*70}")
        print("Summary:")
        print(f"  - {len(users)} users with different metadata")
        print(f"  - {len(auth_urls)} concurrent auth flows")
        print("  - Each user received correct completion")
        print("  - Metadata properly isolated")
        print("  - No cross-contamination between users")
        print(f"{'='*70}\n")


@skip_manual
class TestNonBlockingAuthPattern:
    """
    Integration test for non-blocking authentication pattern from README.

    **THIS TEST CURRENTLY FAILS** - Documents a bug in session status management.

    Expected behavior (from README example):
    ```python
    while session.requires_user_action:
        await asyncio.sleep(1)
    # Status should automatically update when OAuth completes
    ```

    Current bug:
    - OAuth callback completes successfully (token is saved)
    - session.requires_user_action remains True
    - session.status remains AUTH_PENDING
    - Status never auto-updates

    This is a proper TDD test that:
    - ❌ FAILS until the bug is fixed
    - ✅ Will PASS when session status auto-updates correctly

    Test configuration:
    - auto_open_browser=False (don't auto-open browser)
    - block_until_callback=False (return immediately, don't block)

    To run this test:
    1. Set KEYCARD_ZONE_URL environment variable
    2. Run: uv run pytest packages/mcp/tests/integration/interactive/test_client_oauth_flows.py::TestNonBlockingAuthPattern -v -s
    3. Open the printed authorization URL in your browser
    4. Complete OAuth within 10 seconds
    5. Test will FAIL with clear bug description
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)  # 1 minute timeout
    async def test_non_blocking_auth_with_polling(
        self,
        mcp_server: MCPServerFixture,
        storage_backend: InMemoryBackend
    ):
        """
        Test that session status auto-updates when OAuth completes (TDD - currently FAILS).

        Expected: session.requires_user_action changes to False when OAuth completes
        Actual: Status remains AUTH_PENDING even though token is saved

        This test will PASS when the bug is fixed.
        """
        print(f"\n{'='*70}")
        print("Testing Non-Blocking Auth Pattern (README Example)")
        print(f"{'='*70}\n")

        # Configure server
        servers = {
            "my-server": {
                "url": mcp_server.url,
                "transport": "http",
                "auth": {
                    "type": "oauth"
                }
            }
        }

        # Disable auto-open browser and blocking behavior
        coordinator = LocalAuthCoordinator(
            backend=storage_backend,
            host="localhost",
            port=8888,
            callback_path="/oauth/callback",
            auto_open_browser=False,      # Don't auto-open browser
            block_until_callback=False    # Return immediately instead of blocking
        )

        async with Client(servers, auth_coordinator=coordinator) as client:
            print("--- Phase 1: Initial Connection ---")
            # Context manager automatically connects to all servers
            # Connection failures are communicated via status, not exceptions

            session = client.sessions["my-server"]
            print(f"  Session status: {session.status.value}")
            print(f"  is_operational: {session.is_operational}")
            print(f"  requires_user_action: {session.requires_user_action}")

            # Check if authentication is required
            if session.requires_user_action:  # status == AUTH_PENDING
                print("\n--- Phase 2: Authentication Required ---")
                auth_challenges = await client.get_auth_challenges()
                if auth_challenges:
                    auth_url = auth_challenges[0].get("authorization_url")
                    print("\n🔐 Authentication required!")
                    print(f"Please visit: {auth_url}\n")

                    # Open browser manually (since auto_open_browser=False)
                    print("Opening browser...")
                    webbrowser.open(auth_url)

                    await asyncio.sleep(5)

                    # Wait for user to complete auth in browser
                    # (callback server still runs in background)
                    print("\n--- Phase 3: Polling for Authentication (max 10 seconds) ---")
                    print("  Waiting for session.requires_user_action to change...")
                    start_time = asyncio.get_event_loop().time()
                    timeout = 10.0
                    poll_interval = 0.5

                    # EXPECTED BEHAVIOR (from README):
                    # The session status should automatically update when OAuth completes
                    while session.requires_user_action:
                        elapsed = asyncio.get_event_loop().time() - start_time
                        if elapsed >= timeout:
                            # Test FAILS here if status doesn't auto-update
                            print(f"\n❌ Timeout reached after {elapsed:.1f}s")
                            print(f"  Session status: {session.status.value}")
                            print(f"  requires_user_action: {session.requires_user_action}")

                            # Check if auth actually completed
                            oauth_storage = session.context.storage_path() \
                                .for_server("my-server") \
                                .for_connection() \
                                .for_oauth() \
                                .build()
                            token = await oauth_storage.get("tokens")

                            if token:
                                print("\n❌ BUG DETECTED:")
                                print("  - OAuth callback completed (token exists)")
                                print("  - BUT session.requires_user_action is still True")
                                print("  - AND session.status is still auth_pending")
                                print("\nExpected: session status should auto-update when auth completes")
                                print("Actual: status remains auth_pending, requires manual reconnect")

                            raise AssertionError(
                                "session.requires_user_action should become False after OAuth completion. "
                                f"Status remained: {session.status.value}"
                            )

                        await asyncio.sleep(poll_interval)
                        print(f"  ⏳ Polling... ({elapsed:.1f}s elapsed, status: {session.status.value})")

                    # If we get here, status updated correctly!
                    elapsed = asyncio.get_event_loop().time() - start_time
                    print(f"\n✓ requires_user_action changed after {elapsed:.1f}s")
                    print(f"  Session status: {session.status.value}")
                    print(f"  requires_user_action: {session.requires_user_action}")

            # Check if connection succeeded
            print("\n--- Phase 4: Verify Connection ---")
            print(f"  Session status: {session.status.value}")
            print(f"  is_operational: {session.is_operational}")
            print(f"  is_failed: {session.is_failed}")

            if not session.is_operational:
                print(f"❌ Failed to connect: {session.status}")
                raise AssertionError(
                    f"Session should be operational after auth completion. "
                    f"Status: {session.status.value}"
                )

            print("✓ Session is operational after status auto-update")

            # Now authenticated - use the tools
            print("\n--- Phase 5: Test Operations ---")
            tools = await client.list_tools("my-server")
            print(f"✓ Available tools: {len(tools)}")

            # Try calling a tool
            result = await client.call_tool(
                server_name="my-server",
                tool_name="echo",
                arguments={"message": "Hello from non-blocking test!"}
            )
            print("✓ Tool call successful")

            # Verify result
            assert result is not None
            assert hasattr(result, 'content')
            text_content = result.content[0].text if hasattr(result.content[0], 'text') else str(result.content[0])
            assert "Hello from non-blocking test!" in text_content
            print(f"✓ Tool returned expected content: {text_content}")

            print(f"\n{'='*70}")
            print("✅ TEST PASSED - Non-blocking auth pattern works correctly!")
            print("   Status auto-updated after OAuth completion as expected")
            print(f"{'='*70}\n")
