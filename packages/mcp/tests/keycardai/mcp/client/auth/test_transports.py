"""Tests for HTTP authentication transport adapters."""

from typing import Any

import httpx2
import pytest

from keycardai.mcp.client.auth.transports import HttpxAuth


class StubAuthStrategy:
    """Authentication strategy stub for exercising the httpx2 auth flow."""

    def __init__(self, metadata: list[dict[str, Any]], retry: bool = False):
        self.metadata = metadata
        self.retry = retry
        self.challenges: list[httpx2.Response] = []

    async def get_auth_metadata(self) -> dict[str, Any]:
        return self.metadata.pop(0)

    async def handle_challenge(
        self,
        challenge: httpx2.Response,
        resource_url: str,
    ) -> bool:
        self.challenges.append(challenge)
        assert resource_url == "https://example.com/mcp"
        return self.retry


@pytest.mark.asyncio
async def test_httpx_auth_adds_strategy_headers() -> None:
    """The adapter applies strategy metadata through httpx2."""
    strategy = StubAuthStrategy([{"headers": {"X-API-Key": "secret"}}])

    async def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["X-API-Key"] == "secret"
        return httpx2.Response(200)

    async with httpx2.AsyncClient(
        auth=HttpxAuth(strategy),
        transport=httpx2.MockTransport(handle),
    ) as client:
        response = await client.get("https://example.com/mcp")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_httpx_auth_handles_challenge_and_retries() -> None:
    """The adapter passes httpx2 challenges to the strategy before retrying."""
    strategy = StubAuthStrategy(
        [
            {},
            {"headers": {"Authorization": "Bearer refreshed"}},
        ],
        retry=True,
    )
    request_count = 0

    async def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx2.Response(401)

        assert request.headers["Authorization"] == "Bearer refreshed"
        return httpx2.Response(200)

    async with httpx2.AsyncClient(
        auth=HttpxAuth(strategy),
        transport=httpx2.MockTransport(handle),
    ) as client:
        response = await client.get("https://example.com/mcp")

    assert response.status_code == 200
    assert request_count == 2
    assert len(strategy.challenges) == 1
    assert isinstance(strategy.challenges[0], httpx2.Response)
