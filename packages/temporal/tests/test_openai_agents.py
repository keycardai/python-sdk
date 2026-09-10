"""Unit tests for keycardai.temporal.openai_agents: no zone, no OpenAI, no network.

The Keycard client is replaced by a recorder, the clock by a counter, and the
plugin by a stand-in that does what ``ModelActivity`` does: ask the provider
for a model on every call. The one real dependency exercised is the ``openai``
client itself (part of the extra), against an httpx mock transport, because
the whole design rests on it awaiting the key callback before each request.
Tests needing the Agents SDK skip when it is not installed; the workspace
cannot carry it (openai-agents pins mcp<2 while keycardai-mcp needs mcp>=2).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from temporalio.exceptions import ApplicationError
from test_interceptor import AssertionCredential, _clear_credential_env

from keycardai.oauth.exceptions import OAuthProtocolError
from keycardai.oauth.server import ClientSecret
from keycardai.temporal import GrantConfigurationError, openai_agents as koa
from keycardai.temporal.openai_agents import KeycardOpenAIKey, KeycardOpenAIProvider

ZONE = "https://zone.test"
VAULT = "urn:test:openai-key"
SECRET = ClientSecret(("id", "secret"))


class StubOAuthClient:
    """Replaces keycardai.oauth.AsyncClient; each mint returns a new key."""

    mints: list[str] = []
    expires_in: int | None = None
    fail_with: Exception | None = None
    closed: int = 0

    def __init__(self, *args, **kwargs):
        pass

    async def client_credentials_grant(self, resource: str):
        if self.fail_with is not None:
            raise self.fail_with
        self.mints.append(resource)
        return SimpleNamespace(
            access_token=f"sk-minted-{len(self.mints)}", expires_in=self.expires_in
        )

    async def aclose(self):
        type(self).closed += 1


class Clock:
    now = 1000.0

    @classmethod
    def monotonic(cls) -> float:
        return cls.now


@pytest.fixture
def mints(monkeypatch) -> list[str]:
    StubOAuthClient.mints = []
    StubOAuthClient.expires_in = None
    StubOAuthClient.fail_with = None
    StubOAuthClient.closed = 0
    Clock.now = 1000.0
    monkeypatch.setattr(koa, "AsyncClient", StubOAuthClient)
    monkeypatch.setattr(koa.time, "monotonic", Clock.monotonic)
    return StubOAuthClient.mints


def key(**kw) -> KeycardOpenAIKey:
    return KeycardOpenAIKey(ZONE, VAULT, SECRET, **kw)


# --- key resolution -----------------------------------------------------------


async def test_key_is_minted_from_the_vaulted_resource(mints):
    assert await key()() == "sk-minted-1"
    assert mints == [VAULT]


async def test_key_is_reused_inside_the_refresh_window(mints):
    k = key(refresh=timedelta(minutes=5))
    first = await k()
    Clock.now += 299
    assert await k() == first
    assert mints == [VAULT]


async def test_key_refreshes_after_the_window_and_the_new_value_is_used(mints):
    k = key(refresh=timedelta(minutes=5))
    assert await k() == "sk-minted-1"
    Clock.now += 300
    assert await k() == "sk-minted-2"
    assert mints == [VAULT, VAULT]


async def test_a_shorter_expires_in_bounds_the_window(mints):
    StubOAuthClient.expires_in = 30
    k = key(refresh=timedelta(minutes=5))
    await k()
    Clock.now += 31
    assert await k() == "sk-minted-2"


async def test_concurrent_cold_callers_share_one_mint(mints):
    k = key()
    keys = await asyncio.gather(*(k() for _ in range(5)))
    assert set(keys) == {"sk-minted-1"}
    assert mints == [VAULT]


async def test_aclose_closes_the_keycard_client_and_forgets_the_key(mints):
    k = key()
    await k()
    await k.aclose()
    assert StubOAuthClient.closed == 1
    assert await k() == "sk-minted-2"


# --- credential handling, same contract as the interceptor --------------------


def test_non_client_secret_credential_raises_the_pointed_error(mints):
    with pytest.raises(GrantConfigurationError) as ei:
        KeycardOpenAIKey(ZONE, VAULT, AssertionCredential())
    msg = str(ei.value)
    assert "requires a ClientSecret credential" in msg
    assert "AssertionCredential" in msg
    assert VAULT in msg


def test_non_positive_refresh_is_rejected(mints):
    with pytest.raises(GrantConfigurationError, match="refresh must be positive"):
        key(refresh=timedelta(0))


async def test_credential_discovered_from_env(mints, monkeypatch):
    _clear_credential_env(monkeypatch)
    monkeypatch.setenv("KEYCARD_CLIENT_ID", "env-id")
    monkeypatch.setenv("KEYCARD_CLIENT_SECRET", "env-secret")
    assert await KeycardOpenAIKey(ZONE, VAULT)() == "sk-minted-1"


def test_undiscoverable_credential_is_a_configuration_error(mints, monkeypatch):
    _clear_credential_env(monkeypatch)
    with pytest.raises(GrantConfigurationError):
        KeycardOpenAIKey(ZONE, VAULT)


# --- failure classification, same as a client-credentials @grant --------------


async def test_permanent_denial_is_non_retryable_and_names_the_resource(mints):
    StubOAuthClient.fail_with = OAuthProtocolError(error="access_denied")
    with pytest.raises(ApplicationError) as ei:
        await key()()
    assert ei.value.type == "KeycardAccessDenied"
    assert ei.value.non_retryable
    assert VAULT in str(ei.value)


async def test_transient_failure_propagates_for_the_retry_policy(mints):
    StubOAuthClient.fail_with = ConnectionError("zone unreachable")
    with pytest.raises(ConnectionError):
        await key()()
    # nothing cached: the next call mints again once the zone is back
    StubOAuthClient.fail_with = None
    assert await key()() == "sk-minted-1"


# --- the openai seam: the client awaits the callback before every request -----


def mock_openai(seen: list[str]) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"object": "list", "data": []})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


async def test_openai_client_sends_the_minted_key_and_picks_up_a_refresh(mints):
    from openai import AsyncOpenAI

    seen: list[str] = []
    k = key(refresh=timedelta(minutes=5))
    client = AsyncOpenAI(api_key=k, max_retries=0, http_client=mock_openai(seen))
    await client.models.list()
    await client.models.list()
    Clock.now += 300
    await client.models.list()
    assert seen == [
        "Bearer sk-minted-1",
        "Bearer sk-minted-1",
        "Bearer sk-minted-2",
    ]
    assert mints == [VAULT, VAULT]


# --- the provider against a stand-in for the plugin's model activity ----------


class FakeAsyncOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False

    async def close(self):
        self.closed = True


class FakeOpenAIProvider:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False

    def get_model(self, model_name):
        return SimpleNamespace(name=model_name, client=self.kwargs["openai_client"])

    async def aclose(self):
        self.closed = True


class FakeModelActivity:
    """What temporalio's ModelActivity does with a provider, per model call."""

    def __init__(self, model_provider):
        self._model_provider = model_provider

    async def invoke_model_activity(self, input: dict) -> str:
        model = self._model_provider.get_model(input.get("model_name"))
        # The real model would send a request; the openai client would then
        # await api_key. Do the same resolution here.
        return f"{model.name}:{await model.client.kwargs['api_key']()}"


@pytest.fixture
def fake_deps(monkeypatch):
    monkeypatch.setattr(
        koa, "_openai_deps", lambda: (FakeOpenAIProvider, FakeAsyncOpenAI)
    )


async def test_provider_wires_the_key_callback_into_the_openai_client(mints, fake_deps):
    provider = KeycardOpenAIProvider(
        ZONE, VAULT, SECRET, base_url="https://llm.test/v1", use_responses=False
    )
    openai = provider._openai
    assert isinstance(openai.kwargs["api_key"], KeycardOpenAIKey)
    assert openai.kwargs["api_key"] is provider.api_key
    assert openai.kwargs == {
        "api_key": provider.api_key,
        "base_url": "https://llm.test/v1",
        "max_retries": 0,
    }
    assert provider._provider.kwargs == {
        "openai_client": openai,
        "use_responses": False,
    }
    assert mints == []  # nothing minted at worker startup


async def test_model_calls_resolve_the_key_per_call_through_the_provider(
    mints, fake_deps
):
    provider = KeycardOpenAIProvider(ZONE, VAULT, SECRET, refresh=timedelta(minutes=5))
    activity = FakeModelActivity(provider)
    assert await activity.invoke_model_activity({"model_name": "gpt-a"}) == (
        "gpt-a:sk-minted-1"
    )
    assert await activity.invoke_model_activity({"model_name": "gpt-b"}) == (
        "gpt-b:sk-minted-1"
    )
    Clock.now += 300
    assert await activity.invoke_model_activity({"model_name": "gpt-a"}) == (
        "gpt-a:sk-minted-2"
    )
    assert mints == [VAULT, VAULT]


async def test_provider_aclose_releases_everything(mints, fake_deps):
    provider = KeycardOpenAIProvider(ZONE, VAULT, SECRET)
    await provider.api_key()
    await provider.aclose()
    assert provider._provider.closed
    assert provider._openai.closed
    assert StubOAuthClient.closed == 1


def test_provider_with_a_non_client_secret_raises_before_touching_openai(
    mints, fake_deps
):
    with pytest.raises(GrantConfigurationError, match="ClientSecret"):
        KeycardOpenAIProvider(ZONE, VAULT, AssertionCredential())


# --- packaging ----------------------------------------------------------------


def test_missing_extra_is_a_clear_import_error(mints, monkeypatch):
    monkeypatch.setitem(sys.modules, "agents", None)
    monkeypatch.setitem(sys.modules, "agents.models.openai_provider", None)
    with pytest.raises(ImportError, match=r"keycardai-temporal\[openai-agents\]"):
        KeycardOpenAIProvider(ZONE, VAULT, SECRET)


def test_importing_keycardai_temporal_pulls_in_neither_openai_nor_agents():
    probe = (
        "import sys, keycardai.temporal;"
        "print(sorted(m for m in sys.modules"
        " if m.split('.')[0] in ('openai', 'agents')"
        " or m == 'keycardai.temporal.openai_agents'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], check=True, capture_output=True, text=True
    )
    assert out.stdout.strip() == "[]"


# --- with the Agents SDK installed: the real seam -----------------------------


async def test_real_openai_provider_hands_out_models_over_our_client(mints):
    agents = pytest.importorskip("agents")
    provider = KeycardOpenAIProvider(ZONE, VAULT, SECRET, use_responses=False)
    model = provider.get_model("gpt-4o")
    assert isinstance(model, agents.Model)
    assert model._client is provider._openai
    assert provider._openai._api_key_provider is provider.api_key
    assert await provider._openai._refresh_api_key() == "sk-minted-1"


def test_real_plugin_accepts_the_provider(mints):
    plugin_module = pytest.importorskip("temporalio.contrib.openai_agents")
    ModelActivityParameters = plugin_module.ModelActivityParameters
    OpenAIAgentsPlugin = plugin_module.OpenAIAgentsPlugin

    provider = KeycardOpenAIProvider(ZONE, VAULT, SECRET)
    OpenAIAgentsPlugin(
        model_params=ModelActivityParameters(
            start_to_close_timeout=timedelta(seconds=60)
        ),
        model_provider=provider,
    )
    assert mints == []
