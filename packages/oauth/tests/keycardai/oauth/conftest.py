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


@dataclass
class FakeAgent:
    """A stand-in for the Keycard agent serving IssueToken on ``path``.

    ``respond`` decides each answer from the recorded call; it defaults to
    issuing ``agent-token``.
    """

    path: str
    respond: Callable[[AgentCall], Response] = issued
    calls: list[AgentCall] = field(default_factory=list)
    _server: uvicorn.Server | None = None
    _task: asyncio.Task | None = None

    async def _issue_token(self, request: Request) -> Response:
        call = AgentCall(
            headers={k.lower(): v for k, v in request.headers.items()},
            body=json.loads(await request.body()),
        )
        self.calls.append(call)
        return self.respond(call)

    async def start(self) -> None:
        app = Starlette(
            routes=[Route(ISSUE_TOKEN_PATH, self._issue_token, methods=["POST"])]
        )
        config = uvicorn.Config(app, uds=self.path, log_level="warning", lifespan="off")
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        while not self._server.started:
            if self._task.done():
                self._task.result()
            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            await self._task


@pytest.fixture
def short_tmp() -> Iterator[str]:
    """A fresh empty directory with a short path.

    pytest's tmp_path can exceed the platform's Unix socket path limit (104
    bytes on macOS) once a socket name is appended.
    """
    path = tempfile.mkdtemp(prefix="kc")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest_asyncio.fixture
async def agent_server() -> AsyncIterator[Callable[..., Awaitable[FakeAgent]]]:
    """Start fake agents on demand: ``await agent_server(path, respond=...)``.

    Creates the socket's parent directory, as the Keycard CLI does, and stops
    every agent it started at teardown.
    """
    started: list[FakeAgent] = []

    async def start(path: str, respond: Callable[[AgentCall], Response] | None = None) -> FakeAgent:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        agent = FakeAgent(path) if respond is None else FakeAgent(path, respond)
        await agent.start()
        started.append(agent)
        return agent

    yield start
    for agent in started:
        await agent.stop()
