"""MCP Client Connection Example with Keycard OAuth.

Demonstrates connecting to an MCP server as a client using OAuth authentication
with StarletteAuthCoordinator for handling OAuth callbacks.

Key concepts:
- StarletteAuthCoordinator for web-based OAuth callback handling
- Session status lifecycle (connecting -> auth_pending -> connected)
- Auth challenge handling and user redirection
- Tool calling on authenticated servers
"""

import os
from contextlib import asynccontextmanager

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from keycardai.mcp.client import (
    Client,
    SQLiteBackend,
    StarletteAuthCoordinator,
)

# Configuration from environment
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000/mcp")
CALLBACK_HOST = os.getenv("CALLBACK_HOST", "localhost")
CALLBACK_PORT = int(os.getenv("CALLBACK_PORT", "8080"))
CALLBACK_PATH = "/oauth/callback"

# Server configuration - connect to an authenticated MCP server
SERVERS = {
    "hello-world": {
        "url": MCP_SERVER_URL,
        "transport": "streamable-http",
        "auth": {"type": "oauth"},
    }
}

# Storage backend - SQLite persists tokens across restarts
storage = SQLiteBackend("client_auth.db")

# OAuth coordinator - handles callbacks via HTTP endpoint
coordinator = StarletteAuthCoordinator(
    backend=storage,
    redirect_uri=f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}",
)

# Client instance - initialized in lifespan
client: Client | None = None


async def homepage(request: Request) -> HTMLResponse:
    """Display connection status and available actions."""
    if client is None:
        return HTMLResponse("<h1>Client not initialized</h1>", status_code=500)

    session = client.sessions.get("hello-world")
    if session is None:
        return HTMLResponse("<h1>Session not found</h1>", status_code=500)

    # Build status HTML
    html = f"""
    <html>
        <head>
            <title>MCP Client Status</title>
            <style>
                body {{ font-family: system-ui, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }}
                .status {{ padding: 10px; border-radius: 4px; margin: 10px 0; }}
                .connected {{ background: #d4edda; }}
                .pending {{ background: #fff3cd; }}
                .failed {{ background: #f8d7da; }}
                pre {{ background: #f5f5f5; padding: 10px; overflow-x: auto; }}
            </style>
        </head>
        <body>
            <h1>MCP Client Connection Status</h1>
            <div class="status {'connected' if session.is_operational else 'pending' if session.requires_user_action else 'failed'}">
                <strong>Status:</strong> {session.status.value}<br>
                <strong>Operational:</strong> {session.is_operational}<br>
                <strong>Requires User Action:</strong> {session.requires_user_action}
            </div>
    """

    if session.requires_user_action:
        # Need to authenticate - show auth link
        challenges = await client.get_auth_challenges()
        if challenges:
            auth_url = challenges[0].get("authorization_url", "")
            html += f"""
            <h2>Authentication Required</h2>
            <p>Click the button below to authenticate with Keycard:</p>
            <p><a href="{auth_url}" target="_blank" style="display: inline-block; padding: 10px 20px; background: #007bff; color: white; text-decoration: none; border-radius: 4px;">Authenticate</a></p>
            <p><em>After authenticating, <a href="/">refresh this page</a>.</em></p>
            """
    elif session.is_operational:
        # Connected - show tools and allow calling them
        tools = await client.list_tools("hello-world")
        tool_list = "<ul>"
        for tool_info in tools:
            tool_list += f"<li><strong>{tool_info.tool.name}</strong>: {tool_info.tool.description or 'No description'}</li>"
        tool_list += "</ul>"

        html += f"""
        <h2>Connected Successfully!</h2>
        <h3>Available Tools</h3>
        {tool_list}
        <h3>Test Tool Call</h3>
        <form action="/call-tool" method="post">
            <label>Name: <input type="text" name="name" value="World" style="padding: 5px;"></label>
            <button type="submit" style="padding: 5px 15px; margin-left: 10px;">Call hello_world</button>
        </form>
        """
    elif session.is_failed:
        html += """
        <h2>Connection Failed</h2>
        <p>The connection to the MCP server failed.</p>
        <p><a href="/reconnect">Try Reconnecting</a></p>
        """
    else:
        html += """
        <h2>Connecting...</h2>
        <p>Connection in progress. <a href="/">Refresh</a> to check status.</p>
        """

    html += """
        </body>
    </html>
    """

    return HTMLResponse(html)


async def call_tool(request: Request) -> HTMLResponse:
    """Handle tool call form submission."""
    if client is None:
        return RedirectResponse("/", status_code=303)

    session = client.sessions.get("hello-world")
    if session is None or not session.is_operational:
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    name = str(form.get("name", "World"))

    try:
        result = await client.call_tool("hello_world", {"name": name})

        # Extract text content from result
        text_parts = []
        for content in result.content:
            if hasattr(content, "text"):
                text_parts.append(content.text)
        text = "\n".join(text_parts) or "No text content returned"

        return HTMLResponse(f"""
        <html>
            <head><title>Tool Result</title>
            <style>
                body {{ font-family: system-ui, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }}
                .result {{ background: #d4edda; padding: 20px; border-radius: 4px; }}
            </style>
            </head>
            <body>
                <h1>Tool Call Result</h1>
                <div class="result">
                    <strong>hello_world("{name}")</strong>
                    <p>{text}</p>
                </div>
                <p><a href="/">Back to Status</a></p>
            </body>
        </html>
        """)
    except Exception as e:
        return HTMLResponse(f"""
        <html>
            <head><title>Tool Error</title>
            <style>
                body {{ font-family: system-ui, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }}
                .error {{ background: #f8d7da; padding: 20px; border-radius: 4px; }}
            </style>
            </head>
            <body>
                <h1>Tool Call Error</h1>
                <div class="error">
                    <strong>Error:</strong> {str(e)}
                </div>
                <p><a href="/">Back to Status</a></p>
            </body>
        </html>
        """, status_code=500)


async def reconnect(request: Request) -> RedirectResponse:
    """Attempt to reconnect to the server."""
    if client is not None:
        await client.connect("hello-world", force_reconnect=True)
    return RedirectResponse("/", status_code=303)


@asynccontextmanager
async def lifespan(app: Starlette):
    """Initialize and cleanup the MCP client."""
    global client

    # Create client with OAuth configuration
    client = Client(
        servers=SERVERS,
        storage_backend=storage,
        auth_coordinator=coordinator,
    )

    # Attempt initial connection
    # This won't block - if auth is required, status will be AUTH_PENDING
    await client.connect()

    print(f"Client initialized. Session status: {client.sessions['hello-world'].status.value}")

    yield

    # Cleanup
    await client.disconnect()


# Build the Starlette application
app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/", homepage),
        Route("/call-tool", call_tool, methods=["POST"]),
        Route("/reconnect", reconnect),
        # OAuth callback endpoint from coordinator
        Route(CALLBACK_PATH, coordinator.get_completion_endpoint()),
    ],
)


def main():
    """Run the web application."""
    print("MCP Client Connection Example")
    print("=" * 40)
    print(f"MCP Server URL: {MCP_SERVER_URL}")
    print(f"Callback URL:   http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}")
    print()
    print(f"Open http://{CALLBACK_HOST}:{CALLBACK_PORT}/ in your browser")
    print()

    uvicorn.run(app, host=CALLBACK_HOST, port=CALLBACK_PORT)


if __name__ == "__main__":
    main()
