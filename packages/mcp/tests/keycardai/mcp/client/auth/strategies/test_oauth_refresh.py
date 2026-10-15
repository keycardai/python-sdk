"""OAuthStrategy refreshes an expiring or rejected access token instead of forcing a sign-in."""

import asyncio
import time
from typing import Any

import pytest
from httpx import Request, Response

from keycardai.mcp.client.auth.strategies import oauth as strategy_module
from keycardai.mcp.client.auth.strategies.oauth import OAuthStrategy
from keycardai.mcp.client.context import Context
from keycardai.mcp.client.storage import InMemoryBackend, NamespacedStorage
from keycardai.oauth import RefreshGrantError
from keycardai.oauth.types.models import TokenResponse

RESOURCE = "https://mcp.example.com/mcp"
METADATA = {
    "issuer": "https://zone.example.com",
    "authorization_endpoint": "https://zone.example.com/authorize",
    "token_endpoint": "https://zone.example.com/token",
    "registration_endpoint": "https://zone.example.com/register",
}
REGISTRATION = {"client_id": "client-1"}


class Coordinator:
    """Records whether the full authorization flow was started."""

    def __init__(self) -> None:
        self.redirects: list[str] = []
        self.storage = NamespacedStorage(InMemoryBackend(), "coordinator")

    async def get_redirect_uris(self) -> list[str]:
        return ["http://localhost:8080/callback"]

    async def register_completion_route(self, **kwargs: Any) -> None:
        return None

    async def set_auth_pending(self, **kwargs: Any) -> None:
        return None

    async def handle_redirect(self, url: str, metadata: dict[str, Any] | None = None) -> None:
        self.redirects.append(url)


def make_strategy() -> tuple[OAuthStrategy, Coordinator]:
    coordinator = Coordinator()
    storage = NamespacedStorage(InMemoryBackend(), "client:user")
    context = Context(id="user", storage=storage, coordinator=coordinator)
    strategy = OAuthStrategy(
        server_name="docs",
        storage=storage.get_namespace("server:docs").get_namespace("oauth"),
        context=context,
        coordinator=coordinator,
    )
    return strategy, coordinator


async def seed(strategy: OAuthStrategy, tokens: dict[str, Any], registration: dict[str, Any] = REGISTRATION) -> None:
    await strategy.oauth_storage.save_tokens(tokens)
    await strategy.oauth_storage.save_client_registration(registration)
    await strategy.oauth_storage.save_auth_server_metadata(METADATA)


def expiring(**extra: Any) -> dict[str, Any]:
    return {
        "access_token": "old-access",
        "refresh_token": "refresh-1",
        "expires_in": 3600,
        "expires_at": time.time() + 30,
        "resource": RESOURCE,
        "issuer": METADATA["issuer"],
        **extra,
    }


class FakeRefresh:
    """Stands in for keycardai.oauth.pkce.refresh_authorization."""

    def __init__(self, result: TokenResponse | Exception, delay: float = 0.0) -> None:
        self.result = result
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> TokenResponse:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def challenge() -> Response:
    return Response(
        401,
        headers={"WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"'},
        request=Request("POST", RESOURCE),
    )


def patch_refresh(monkeypatch: pytest.MonkeyPatch, fake: FakeRefresh) -> None:
    monkeypatch.setattr(strategy_module, "refresh_authorization", fake)


@pytest.mark.asyncio
async def test_near_expiry_refreshes_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy, coordinator = make_strategy()
    await seed(strategy, expiring())
    fake = FakeRefresh(TokenResponse(access_token="new-access", expires_in=3600))
    patch_refresh(monkeypatch, fake)

    metadata = await strategy.get_auth_metadata()

    assert metadata["headers"]["Authorization"] == "Bearer new-access"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["refresh_token"] == "refresh-1"
    assert call["client_id"] == "client-1"
    assert "client_secret" not in call
    assert call["resources"] == [RESOURCE]
    assert call["metadata"].token_endpoint == METADATA["token_endpoint"]
    stored = await strategy.oauth_storage.get_tokens()
    assert stored is not None
    assert stored["access_token"] == "new-access"
    assert stored["expires_at"] >= time.time() + 3500
    assert coordinator.redirects == []


@pytest.mark.asyncio
async def test_fresh_token_is_sent_without_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy, _ = make_strategy()
    await seed(strategy, expiring(expires_at=time.time() + 3600))
    fake = FakeRefresh(TokenResponse(access_token="new-access"))
    patch_refresh(monkeypatch, fake)

    metadata = await strategy.get_auth_metadata()

    assert metadata["headers"]["Authorization"] == "Bearer old-access"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_401_refreshes_once_and_reports_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy, coordinator = make_strategy()
    await seed(strategy, expiring(expires_at=time.time() + 3600))
    fake = FakeRefresh(TokenResponse(access_token="new-access", expires_in=3600))
    patch_refresh(monkeypatch, fake)

    handled = await strategy.handle_challenge(challenge(), RESOURCE)

    assert handled is True
    assert len(fake.calls) == 1
    assert fake.calls[0]["resources"] == [RESOURCE]
    assert coordinator.redirects == []
    assert (await strategy.get_auth_metadata())["headers"]["Authorization"] == "Bearer new-access"


@pytest.mark.asyncio
async def test_invalid_grant_clears_tokens_and_starts_the_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy, coordinator = make_strategy()
    await seed(strategy, expiring())
    patch_refresh(monkeypatch, FakeRefresh(RefreshGrantError("invalid_grant", "expired")))
    started: list[str] = []

    async def discover_resource(response: Response) -> dict[str, Any]:
        return {"authorization_servers": [METADATA["issuer"]]}

    async def discover_auth_server(resource_metadata: dict[str, Any]) -> dict[str, Any]:
        return METADATA

    async def initiate_flow(**kwargs: Any) -> Any:
        started.append("flow")

        class Flow:
            state = "state-1"
            authorization_url = "https://zone.example.com/authorize?state=state-1"

        return Flow()

    async def get_or_register_client(**kwargs: Any) -> dict[str, Any]:
        return REGISTRATION

    monkeypatch.setattr(strategy.discovery, "discover_resource", discover_resource)
    monkeypatch.setattr(strategy.discovery, "discover_auth_server", discover_auth_server)
    monkeypatch.setattr(strategy.registration, "get_or_register_client", get_or_register_client)
    monkeypatch.setattr(strategy.flow_initiator, "initiate_flow", initiate_flow)

    handled = await strategy.handle_challenge(challenge(), RESOURCE)

    assert handled is True
    assert started == ["flow"]
    assert coordinator.redirects == ["https://zone.example.com/authorize?state=state-1"]
    assert await strategy.oauth_storage.get_tokens() is None


@pytest.mark.asyncio
async def test_transient_refresh_error_is_raised_and_starts_no_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy, coordinator = make_strategy()
    await seed(strategy, expiring())
    patch_refresh(monkeypatch, FakeRefresh(RefreshGrantError("temporarily_unavailable")))

    with pytest.raises(RefreshGrantError):
        await strategy.handle_challenge(challenge(), RESOURCE)
    with pytest.raises(RefreshGrantError):
        await strategy.get_auth_metadata()

    assert coordinator.redirects == []
    stored = await strategy.oauth_storage.get_tokens()
    assert stored is not None and stored["refresh_token"] == "refresh-1"


@pytest.mark.asyncio
async def test_rotated_refresh_token_replaces_and_unrotated_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy, _ = make_strategy()
    await seed(strategy, expiring())
    patch_refresh(monkeypatch, FakeRefresh(TokenResponse(access_token="a2", refresh_token="refresh-2", expires_in=10)))
    await strategy.get_auth_metadata()
    stored = await strategy.oauth_storage.get_tokens()
    assert stored is not None and stored["refresh_token"] == "refresh-2"

    patch_refresh(monkeypatch, FakeRefresh(TokenResponse(access_token="a3", expires_in=3600)))
    await strategy.get_auth_metadata()
    stored = await strategy.oauth_storage.get_tokens()
    assert stored is not None
    assert stored["access_token"] == "a3"
    assert stored["refresh_token"] == "refresh-2"


@pytest.mark.asyncio
async def test_no_refresh_token_behaves_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy, coordinator = make_strategy()
    tokens = expiring()
    del tokens["refresh_token"]
    await seed(strategy, tokens)
    fake = FakeRefresh(TokenResponse(access_token="new-access"))
    patch_refresh(monkeypatch, fake)

    assert (await strategy.get_auth_metadata())["headers"]["Authorization"] == "Bearer old-access"
    # The challenge path runs discovery, which has no server here, so it reports unhandled as today.
    assert await strategy.handle_challenge(challenge(), RESOURCE) is False
    assert fake.calls == []
    assert coordinator.redirects == []


@pytest.mark.asyncio
async def test_tokens_saved_without_expiry_are_sent_as_they_are(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy, _ = make_strategy()
    await seed(strategy, {"access_token": "legacy", "refresh_token": "refresh-1", "expires_in": 1})
    fake = FakeRefresh(TokenResponse(access_token="new-access", expires_in=3600))
    patch_refresh(monkeypatch, fake)

    assert (await strategy.get_auth_metadata())["headers"]["Authorization"] == "Bearer legacy"
    assert fake.calls == []
    # The 401 path still refreshes a legacy token, with the challenge's resource.
    assert await strategy.handle_challenge(challenge(), RESOURCE) is True
    assert fake.calls[0]["resources"] == [RESOURCE]


@pytest.mark.asyncio
async def test_two_concurrent_requests_refresh_once(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy, _ = make_strategy()
    await seed(strategy, expiring())
    fake = FakeRefresh(TokenResponse(access_token="new-access", expires_in=3600), delay=0.05)
    patch_refresh(monkeypatch, fake)

    first, second = await asyncio.gather(strategy.get_auth_metadata(), strategy.get_auth_metadata())

    assert first == second == {"headers": {"Authorization": "Bearer new-access"}}
    assert len(fake.calls) == 1
