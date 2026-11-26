"""Fixtures shared by the oauth package's tests.

``agent_server`` serves a fake Keycard agent, a Starlette app under uvicorn,
on a real Unix socket, so the local agent token source's dialer is exercised
without the ``keycard`` binary.
"""

import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

ISSUE_TOKEN_PATH = "/keycard.agent.v1.AgentService/IssueToken"


@dataclass
class AgentCall:
    """One IssueToken request as the fake agent received it."""

    headers: dict[str, str]
    body: dict


def issued(_: AgentCall) -> Response:
    return JSONResponse({"token": "agent-token"})


@asynccontextmanager
async def serve_on_uds(app: Starlette, path: str) -> AsyncIterator[None]:
    """Serve ``app`` under uvicorn on the Unix socket ``path`` until exit."""
    server = uvicorn.Server(
        uvicorn.Config(app, uds=path, log_level="warning", lifespan="off")
    )
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if task.done():
                task.result()
                raise RuntimeError(f"uvicorn stopped before serving {path}")
            await asyncio.sleep(0.01)
        yield
    finally:
        server.should_exit = True
        await task


@dataclass
class FakeAgent:
    """A stand-in for the Keycard agent serving IssueToken on ``path``.

    ``respond`` decides each answer from the recorded call; it defaults to
    issuing ``agent-token``.
    """

    path: str
    respond: Callable[[AgentCall], Response] = issued
    calls: list[AgentCall] = field(default_factory=list)

    async def _issue_token(self, request: Request) -> Response:
        call = AgentCall(
            headers={k.lower(): v for k, v in request.headers.items()},
            body=json.loads(await request.body()),
        )
        self.calls.append(call)
        return self.respond(call)

    def app(self) -> Starlette:
        return Starlette(
            routes=[Route(ISSUE_TOKEN_PATH, self._issue_token, methods=["POST"])]
        )


@pytest.fixture
def short_tmp() -> Iterator[str]:
    """A fresh empty directory with a short path.

    pytest's tmp_path, and on macOS TMPDIR itself, leave too little of the
    Unix socket path limit (104 bytes on macOS) for the deepest default
    socket path, so the directory is made under /tmp where that exists.
    """
    path = tempfile.mkdtemp(prefix="kc", dir="/tmp" if os.path.isdir("/tmp") else None)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def uds_server() -> Callable[[Starlette, str], AbstractAsyncContextManager[None]]:
    """``async with uds_server(app, path):`` serves ``app`` on ``path``."""
    return serve_on_uds


@pytest_asyncio.fixture
async def agent_server() -> AsyncIterator[Callable[..., Awaitable[FakeAgent]]]:
    """Start fake agents on demand: ``await agent_server(path, respond=...)``.

    Creates the socket's parent directory, as the Keycard CLI does, and stops
    every agent it started at teardown, even when stopping one fails.
    """
    async with AsyncExitStack() as stack:

        async def start(
            path: str, respond: Callable[[AgentCall], Response] | None = None
        ) -> FakeAgent:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            agent = FakeAgent(path) if respond is None else FakeAgent(path, respond)
            await stack.enter_async_context(serve_on_uds(agent.app(), path))
            return agent

        yield start
