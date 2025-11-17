"""
Shared fixtures for interactive integration tests.

This module provides centralized fixture management for:
- MCP server (session-scoped, shared across all tests)
- Callback servers (function-scoped, dynamic port allocation)
- Storage backends
- Zone URL configuration

Port allocation strategy:
- MCP Server: 8765 (session-scoped, shared)
- Callback servers: Dynamic allocation starting from 8080
"""

import asyncio
import multiprocessing
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import pytest_asyncio
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route

from keycardai.mcp.client import (
    InMemoryBackend,
    SQLiteBackend,
    StarletteAuthCoordinator,
)

# Export classes for type hints in test files
__all__ = [
    "MCPServerFixture",
    "CallbackServerFixture",
    "PortManager",
]

# ===== Port Manager =====


class PortManager:
    """Manages dynamic port allocation for callback servers."""

    def __init__(self, start_port: int = 8080):
        self._start_port = start_port
        self._current_port = start_port
        self._used_ports = set()

    def allocate(self) -> int:
        """Allocate a new port."""
        while self._current_port in self._used_ports:
            self._current_port += 1

        port = self._current_port
        self._used_ports.add(port)
        self._current_port += 1
        return port

    def release(self, port: int):
        """Release a port back to the pool."""
        self._used_ports.discard(port)


# Global port manager instance
_port_manager = PortManager()


# ===== MCP Server Setup =====


def run_mcp_server(port: int, zone_url: str):
    """
    Run MCP server in a subprocess.

    This server provides:
    - echo(message: str) -> str: Echoes back a message
    - get_user_info() -> dict: Returns user info from token claims
    - add_numbers(a: int, b: int) -> int: Adds two numbers
    """
    from fastmcp import FastMCP
    from fastmcp.server.dependencies import get_access_token

    from keycardai.mcp.integrations.fastmcp import AuthProvider

    auth = AuthProvider(
        zone_url=zone_url,
        mcp_server_url=f"http://localhost:{port}/mcp"
    )

    server = FastMCP(
        name="Test-MCP-Server",
        auth=auth.get_remote_auth_provider()
    )

    @server.tool()
    def get_user_info() -> dict:
        """Get user information from access token claims."""
        access_token = get_access_token()
        return {
            "claims": access_token.claims
        }

    @server.tool()
    def echo(message: str) -> str:
        """Echo back a message."""
        return f"Echo: {message}"

    @server.tool()
    def add_numbers(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    # Run server
    server.run(transport="http", host="localhost", port=port)


class MCPServerFixture:
    """Fixture to manage MCP server lifecycle."""

    def __init__(self, port: int, zone_url: str):
        self.port = port
        self.zone_url = zone_url
        self.process = None

    def start(self):
        """Start the MCP server in a subprocess."""
        self.process = multiprocessing.Process(
            target=run_mcp_server,
            args=(self.port, self.zone_url)
        )
        self.process.start()

        # Wait for server to be ready
        time.sleep(2)

    def stop(self):
        """Stop the MCP server."""
        if self.process:
            self.process.terminate()
            self.process.join(timeout=5)
            if self.process.is_alive():
                self.process.kill()
            self.process = None

    @property
    def url(self):
        """Get the MCP server URL."""
        return f"http://localhost:{self.port}/mcp"


# ===== Callback Server Setup =====


class CallbackServerFixture:
    """Fixture to run a Starlette callback server for OAuth."""

    def __init__(self, coordinator: StarletteAuthCoordinator, port: int):
        self.coordinator = coordinator
        self.port = port
        self.server_task = None
        self.server = None

    async def start(self):
        """Start the callback server."""
        # Create Starlette app with OAuth callback/completion route
        app = Starlette(
            routes=[
                Route("/callback", self.coordinator.get_completion_endpoint(), methods=["GET", "POST"]),
            ]
        )

        # Configure uvicorn with proper socket reuse
        config = uvicorn.Config(
            app,
            host="localhost",
            port=self.port,
            log_level="warning",
            timeout_graceful_shutdown=1,  # Shorter graceful shutdown
        )
        self.server = uvicorn.Server(config)

        # Start server in background task
        self.server_task = asyncio.create_task(self.server.serve())

        # Wait a bit for server to start
        await asyncio.sleep(1)

    async def stop(self):
        """Stop the callback server gracefully."""
        if self.server:
            self.server.should_exit = True

        if self.server_task:
            try:
                # Wait for server to shutdown with timeout
                await asyncio.wait_for(
                    asyncio.shield(self.server_task),
                    timeout=2.0
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # Force cancel if it doesn't stop gracefully
                if not self.server_task.done():
                    self.server_task.cancel()
                    try:
                        await self.server_task
                    except asyncio.CancelledError:
                        pass

        # Extra time for OS to release the port
        await asyncio.sleep(0.2)


# ===== Pytest Fixtures =====


@pytest.fixture(scope="session")
def mcp_server_port():
    """Configurable MCP server port (session-scoped)."""
    return int(os.getenv("MCP_TEST_PORT", "8765"))


@pytest.fixture(scope="session")
def zone_url():
    """Keycard zone URL for testing (session-scoped)."""
    url = os.getenv("KEYCARD_ZONE_URL")
    if not url:
        pytest.skip("KEYCARD_ZONE_URL environment variable not set")
    return url


@pytest.fixture(scope="session")
def mcp_server(mcp_server_port, zone_url):
    """
    Fixture that starts and stops a real MCP server (session-scoped).

    This server is shared across all tests to avoid port conflicts.
    """
    server = MCPServerFixture(mcp_server_port, zone_url)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def storage_backend():
    """Fixture providing an in-memory storage backend (function-scoped)."""
    return InMemoryBackend()


@pytest.fixture
def callback_port():
    """
    Allocate a unique port for callback server (function-scoped).

    Each test gets its own port to avoid conflicts.
    """
    port = _port_manager.allocate()
    yield port
    _port_manager.release(port)


@pytest_asyncio.fixture
async def callback_server(storage_backend, callback_port):
    """
    Fixture that starts and stops a callback server (function-scoped).

    Each test gets its own callback server on a unique port.
    """
    coordinator = StarletteAuthCoordinator(
        redirect_uri=f"http://localhost:{callback_port}/callback",
        backend=storage_backend
    )

    server = CallbackServerFixture(coordinator, port=callback_port)
    await server.start()

    yield coordinator, server

    await server.stop()


@pytest.fixture
def temp_db_path():
    """Fixture providing a temporary SQLite database path (function-scoped)."""
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "serverless_test.db"
        yield db_path
        # Cleanup handled by TemporaryDirectory


@pytest_asyncio.fixture
async def callback_server_sqlite(temp_db_path, callback_port):
    """
    Fixture that starts and stops a callback server with SQLite backend (function-scoped).

    Each test gets its own callback server with a unique port and separate SQLite database.
    """
    storage_backend = SQLiteBackend(temp_db_path)
    coordinator = StarletteAuthCoordinator(
        redirect_uri=f"http://localhost:{callback_port}/callback",
        backend=storage_backend
    )

    server = CallbackServerFixture(coordinator, port=callback_port)
    await server.start()

    yield coordinator, server

    await server.stop()
    await storage_backend.close()

