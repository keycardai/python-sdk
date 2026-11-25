"""Middleware rows of the spec's unit table, through the fake Hermes chain."""

from __future__ import annotations

import asyncio
import contextvars
import json
import multiprocessing
import os
import stat
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from keycardai.hermes import (
    Access,
    HermesConfigurationError,
    agrant,
    get_access_context,
    grant,
)
from keycardai.hermes.settings import PLUGIN_ID

from .conftest import OTHER, RESOURCE, SIGN_IN_RESOURCE, Harness, base_settings
from .hermes_fakes import FakeSession, invalid_grant, network_error, server_error


def tool_reading(resource: str = RESOURCE):
    def tool(args: dict[str, Any]) -> str:
        access = get_access_context()
        if access.has_error():
            return f"ERROR:{(access.get_error() or {}).get('code')}"
        if access.has_resource_error(resource):
            return f"RESOURCE_ERROR:{(access.get_resource_error(resource) or {}).get('code')}"
        if resource not in access.get_successful_resources():
            return "NO_TOKEN"
        return access.access(resource).access_token

    return tool


def ran(calls: list[str]):
    def tool(args: dict[str, Any]) -> str:
        calls.append("ran")
        return "tool-output"

    return tool


# row 1
def test_row_1_missing_sign_in_blocks_with_the_login_command(harness: Harness) -> None:
    calls: list[str] = []
    result = harness.call("calendar", ran(calls))
    assert calls == []
    assert result.startswith("sign_in_required: the tool calendar")
    assert "reason: missing_identity" in result
    assert "hermes keycard login --actor owner" in result
    assert harness.client.exchange_calls == []


# row 2
def test_row_2_engine_failure_returns_a_tool_result_and_never_raises(harness: Harness) -> None:
    harness.store_sign_in("owner")
    harness.client.exchange_raises = None
    harness.plugin.grants.engine.build_access = _raising  # type: ignore[method-assign]
    calls: list[str] = []
    result = harness.call("calendar", ran(calls))
    assert calls == []
    assert isinstance(result, str) and "calendar" in result and "RuntimeError" in result
    assert harness.failures == []


async def _raising(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("engine down")


# row 3
def test_row_3_actor_id_is_used_and_user_id_never_read(harness: Harness) -> None:
    harness.store_sign_in("alice", "alice-token")
    harness.store_sign_in("bob", "bob-token")
    tokens = FakeSession.bind(HERMES_SESSION_ACTOR_ID="alice", HERMES_SESSION_USER_ID="bob")
    try:
        result = harness.call("calendar", tool_reading())
    finally:
        FakeSession.unbind(tokens)
    assert result == f"obo-alice-token-for-{RESOURCE}"
    assert "HERMES_SESSION_USER_ID" not in harness.session.reads


# row 4
def test_row_4_cli_with_no_session_vars_uses_owner_actor(harness: Harness) -> None:
    harness.store_sign_in("owner", "owner-token")
    assert harness.call("calendar", tool_reading()) == f"obo-owner-token-for-{RESOURCE}"


# row 5
def test_row_5_no_session_vars_and_no_owner_actor(make_harness) -> None:
    settings = base_settings()
    del settings["owner_actor"]
    blocked = make_harness(settings)
    calls: list[str] = []
    result = blocked.call("calendar", ran(calls))
    assert calls == [] and "reason: missing_identity" in result
    settings["block_without_sign_in"] = False
    recorded = make_harness(settings)
    assert recorded.call("calendar", tool_reading()) == "ERROR:missing_identity"


# row 6
def test_row_6_mcp_toolset_acquires_nothing(make_harness) -> None:
    h = make_harness(base_settings(resources=[RESOURCE]), toolsets={"mcp_calendar": "mcp-calendar"})

    def tool(args: dict[str, Any]) -> str:
        access = get_access_context()
        return "EMPTY" if not access.get_successful_resources() and not access.has_errors() else "FULL"

    assert h.call("mcp_calendar", tool) == "EMPTY"
    assert h.client.exchange_calls == [] and h.client.self_calls == []


# row 7
def test_row_7_host_isolation_refuses_to_load(tmp_path) -> None:
    from keycardai.hermes.plugin import register

    from .hermes_fakes import FakePluginContext

    ctx = FakePluginContext(base_settings())
    with pytest.raises(HermesConfigurationError) as info:
        register(ctx, isolation_mode=lambda: "host", hermes_home=lambda: tmp_path, get_secret=lambda n: "s")
    assert "plugins.isolation" in str(info.value)
    assert not ctx.registered_anything


# row 11
def test_row_11_refresh_rejected_with_invalid_grant_routes_to_sign_in(harness: Harness) -> None:
    harness.store_sign_in("owner", "old", expires_in=10)
    harness.client.refresh_error = invalid_grant()
    calls: list[str] = []
    result = harness.call("calendar", ran(calls))
    assert calls == []
    assert "reason: subject_token_expired" in result
    assert harness.client.exchange_calls == []


# row 12
def test_row_12_concurrent_turns_exchange_their_own_actors_token(harness: Harness) -> None:
    harness.store_sign_in("alice", "alice-token")
    harness.store_sign_in("bob", "bob-token")
    results: dict[str, str] = {}
    gate = threading.Barrier(2)

    def turn(actor: str) -> None:
        tokens = FakeSession.bind(HERMES_SESSION_ACTOR_ID=actor)
        try:
            gate.wait()
            results[actor] = harness.call("calendar", tool_reading())
        finally:
            FakeSession.unbind(tokens)

    threads = [threading.Thread(target=turn, args=(a,)) for a in ("alice", "bob")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {
        "alice": f"obo-alice-token-for-{RESOURCE}",
        "bob": f"obo-bob-token-for-{RESOURCE}",
    }


# row 13
def test_row_13_sync_threadpool_and_async_tools_see_the_context(harness: Harness) -> None:
    harness.store_sign_in("owner", "owner-token")
    expected = f"obo-owner-token-for-{RESOURCE}"

    def pooled(args: dict[str, Any]) -> str:
        with ThreadPoolExecutor(1) as pool:
            return pool.submit(contextvars.copy_context().run, tool_reading(), args).result()

    def asynchronous(args: dict[str, Any]) -> str:
        async def body() -> str:
            return tool_reading()(args)

        return asyncio.run(body())

    assert harness.call("calendar", tool_reading()) == expected
    assert harness.call("calendar", pooled) == expected
    assert harness.call("calendar", asynchronous) == expected


# row 14
def test_row_14_denied_grant_with_authorization_url_blocks(make_harness) -> None:
    h = make_harness(base_settings(authorization_url="https://consent.example.test/grant"))
    h.store_sign_in("owner")
    h.client.denied_resources.add(OTHER)
    calls: list[str] = []
    result = h.call("two", ran(calls))
    assert calls == []
    assert result.startswith("authorization_required: the tool two")
    assert "https://consent.example.test/grant" in result


# row 15
def test_row_15_as_self_with_denied_grant_records_and_runs(make_harness) -> None:
    h = make_harness(base_settings(default_pattern="as_self", authorization_url="https://consent.example.test"))
    h.client.self_granted = False
    assert h.call("calendar", tool_reading()).startswith("RESOURCE_ERROR:")


# row 16
def test_row_16_grant_and_agrant_outside_a_run(harness: Harness) -> None:
    harness.store_sign_in("owner", "owner-token")
    with grant(tool_name="calendar"):
        assert get_access_context().access(RESOURCE).access_token == f"obo-owner-token-for-{RESOURCE}"
    with grant(Access.on_behalf_of("explicit"), resources=[OTHER]) as access:
        assert access.access(OTHER).access_token == f"obo-explicit-for-{OTHER}"

    async def body() -> str:
        async with agrant(tool_name="calendar"):
            return get_access_context().access(RESOURCE).access_token

    assert asyncio.run(body()) == f"obo-owner-token-for-{RESOURCE}"
    with pytest.raises(RuntimeError):
        get_access_context()


# row 17
def test_row_17_post_gateway_admission_asks_for_sign_in(make_harness) -> None:
    h = make_harness(base_settings(sign_in_on_admission=True))
    hook = h.ctx.hooks["post_gateway_admission"][0]
    # Hermes consumes a message only for {"action": "handled"} (gateway/run_inbound_consumer.py:53-58).
    result = hook(session_key="k", platform="telegram", source={"user_id": "carol"}, message_id="1", text="hi")
    assert result is not None and result["action"] == "handled"
    assert "hermes keycard login --actor carol" in result["reply"]
    h.store_sign_in("carol")
    assert hook(session_key="k", platform="telegram", source={"user_id": "carol"}, message_id="2", text="hi") is None


def test_admission_hook_is_off_by_default(harness: Harness) -> None:
    assert "post_gateway_admission" not in harness.ctx.hooks


def test_sign_ins_of_colliding_actor_ids_never_share_a_file(harness: Harness) -> None:
    store = harness.plugin.grants.store
    harness.store_sign_in("alice@example.com", "at-token")
    harness.store_sign_in("alice_example.com", "underscore-token")
    assert store.load("alice@example.com").access_token == "at-token"
    assert store.load("alice_example.com").access_token == "underscore-token"
    assert store.path("alice@example.com") != store.path("alice_example.com")
    store.path("bob").write_text(store.path("alice@example.com").read_text())
    assert store.load("bob") is None


# row 18
def test_row_18_near_expiry_refreshes_as_the_public_client_then_exchanges(harness: Harness) -> None:
    harness.store_sign_in("owner", "old", expires_in=10, client_id="public-7")
    result = harness.call("calendar", tool_reading())
    assert len(harness.client.refresh_calls) == 1
    call = harness.client.refresh_calls[0]
    assert call["client_id"] == "public-7" and call["refresh_token"] == "refresh-1"
    assert call["resources"] == [SIGN_IN_RESOURCE]
    assert result == f"obo-access-1-for-{RESOURCE}"
    assert harness.client.exchange_calls[0].subject_token == "access-1"


# row 19
def test_row_19_rotated_refresh_token_is_stored_by_rename_at_0600(harness: Harness) -> None:
    harness.store_sign_in("owner", "old", expires_in=10)
    path = harness.plugin.grants.store.path("owner")
    inode_before = path.stat().st_ino
    order: list[str] = []
    original_exchange = harness.client.exchange_token

    async def exchange(request):
        order.append("exchange:" + json.loads(path.read_text())["refresh_token"])
        return await original_exchange(request)

    harness.client.exchange_token = exchange  # type: ignore[method-assign]
    harness.call("calendar", tool_reading())
    assert order == ["exchange:refresh-2"]
    assert path.stat().st_ino != inode_before
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob(".*.tmp"))


# row 20 (threads)
def test_row_20_two_threads_share_one_refresh(harness: Harness) -> None:
    harness.store_sign_in("owner", "old", expires_in=10)
    harness.client.refresh_delay = 0.2
    results: list[str] = []
    gate = threading.Barrier(2)

    def turn() -> None:
        gate.wait()
        results.append(harness.call("calendar", tool_reading()))

    threads = [threading.Thread(target=turn) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(harness.client.refresh_calls) == 1
    assert results == [f"obo-access-1-for-{RESOURCE}"] * 2


def _other_process_refreshes(home: str, zone: str, hold: float, locked: Any) -> None:
    from keycardai.hermes.store import SignIn, SignInStore

    store = SignInStore(home, zone)
    with store.lock("owner"):
        locked.set()
        time.sleep(hold)
        store.save("owner", SignIn("public-1", "refresh-other", "access-other", time.time() + 3600))


# row 20 (processes)
def test_row_20_two_processes_share_one_refresh(harness: Harness) -> None:
    harness.store_sign_in("owner", "old", expires_in=10)
    store = harness.plugin.grants.store
    ctx = multiprocessing.get_context("spawn")
    locked = ctx.Event()
    child = ctx.Process(target=_other_process_refreshes, args=(str(store.home), store.zone_url, 0.5, locked))
    child.start()
    assert locked.wait(timeout=20)
    result = harness.call("calendar", tool_reading())
    child.join(timeout=5)
    assert harness.client.refresh_calls == []
    assert result == f"obo-access-other-for-{RESOURCE}"


async def _ticking(ticks: list[float], until: asyncio.Event) -> None:
    while not until.is_set():
        ticks.append(time.monotonic())
        await asyncio.sleep(0.01)


def test_agrant_refresh_leaves_the_loop_running(harness: Harness) -> None:
    harness.store_sign_in("owner", "old", expires_in=10)
    harness.client.refresh_delay = 0.3

    async def body() -> tuple[str, int]:
        ticks: list[float] = []
        done = asyncio.Event()
        ticker = asyncio.create_task(_ticking(ticks, done))
        await asyncio.sleep(0)
        async with agrant(tool_name="calendar"):
            token = get_access_context().access(RESOURCE).access_token
        done.set()
        await ticker
        return token, len(ticks)

    token, ticks = asyncio.run(body())
    assert token == f"obo-access-1-for-{RESOURCE}"
    assert len(harness.client.refresh_calls) == 1
    # A 0.3s refresh at a 0.01s tick: a blocked loop ticks once.
    assert ticks >= 10


def test_agrant_waits_on_another_holders_refresh_without_blocking_the_loop(harness: Harness) -> None:
    harness.store_sign_in("owner", "old", expires_in=10)
    store = harness.plugin.grants.store
    locked = threading.Event()

    def other_holder() -> None:
        with store.lock("owner"):
            locked.set()
            time.sleep(0.3)
            harness.store_sign_in("owner", "access-other", expires_in=3600)

    holder = threading.Thread(target=other_holder)
    holder.start()
    assert locked.wait(timeout=5)

    async def body() -> tuple[str, int]:
        ticks: list[float] = []
        done = asyncio.Event()
        ticker = asyncio.create_task(_ticking(ticks, done))
        await asyncio.sleep(0)
        async with agrant(tool_name="calendar"):
            token = get_access_context().access(RESOURCE).access_token
        done.set()
        await ticker
        return token, len(ticks)

    token, ticks = asyncio.run(body())
    holder.join(timeout=5)
    assert harness.client.refresh_calls == []
    assert token == f"obo-access-other-for-{RESOURCE}"
    assert ticks >= 10


def test_two_agrants_on_one_loop_share_one_refresh(harness: Harness) -> None:
    harness.store_sign_in("owner", "old", expires_in=10)
    harness.client.refresh_delay = 0.2

    async def one() -> str:
        async with agrant(tool_name="calendar"):
            return get_access_context().access(RESOURCE).access_token

    async def body() -> list[str]:
        return list(await asyncio.gather(one(), one()))

    assert asyncio.run(body()) == [f"obo-access-1-for-{RESOURCE}"] * 2
    assert len(harness.client.refresh_calls) == 1


@pytest.mark.parametrize(
    ("error", "expected"),
    [(invalid_grant(), "subject_token_expired"), (network_error(), "refresh_failed")],
)
def test_agrant_routes_refresh_failures_like_the_sync_path(harness: Harness, error, expected) -> None:
    harness.store_sign_in("owner", "old", expires_in=10)
    before = harness.plugin.grants.store.path("owner").read_text()
    harness.client.refresh_error = error

    async def body() -> Any:
        async with agrant(tool_name="calendar") as access:
            return access

    access = asyncio.run(body())
    if expected == "subject_token_expired":
        assert access.get_error()["code"] == expected
    else:
        assert access.get_resource_error(RESOURCE)["code"] == expected
    assert harness.plugin.grants.store.path("owner").read_text() == before
    assert harness.client.exchange_calls == []


def test_cancelled_alock_waiter_leaves_the_lock_free(harness: Harness) -> None:
    store = harness.plugin.grants.store
    harness.store_sign_in("owner")

    async def body() -> None:
        async with store.alock("owner"):
            waiter = asyncio.create_task(_take(store))
            await asyncio.sleep(0.05)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        async with asyncio.timeout(1):
            async with store.alock("owner"):
                pass

    asyncio.run(body())
    with store.lock("owner"):
        pass


async def _take(store: Any) -> None:
    async with store.alock("owner"):
        pass


def test_cancelled_agrant_still_saves_the_rotated_refresh_token(harness: Harness) -> None:
    harness.store_sign_in("owner", "old", expires_in=10)
    harness.client.refresh_delay = 0.2
    store = harness.plugin.grants.store

    async def one() -> None:
        async with agrant(tool_name="calendar"):
            pass

    async def body() -> Any:
        task = asyncio.create_task(one())
        # The zone has the request (and rotates refresh-1 to refresh-2) before
        # the caller is cancelled.
        while not harness.client.refresh_calls:
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Cancellation reaches the caller only once the replacement is saved.
        return store.load("owner")

    saved = asyncio.run(body())
    assert saved is not None
    assert (saved.refresh_token, saved.access_token) == ("refresh-2", "access-1")

    harness.now += 600
    with grant(tool_name="calendar") as access:
        token = access.access(RESOURCE).access_token
    assert [call["refresh_token"] for call in harness.client.refresh_calls] == ["refresh-1", "refresh-2"]
    assert token == f"obo-access-2-for-{RESOURCE}"


def test_agrant_timeout_still_saves_the_refresh_and_raises_timeout(harness: Harness) -> None:
    harness.store_sign_in("owner", "old", expires_in=10)
    harness.client.refresh_delay = 0.2

    async def body() -> None:
        async with asyncio.timeout(0.05):
            async with agrant(tool_name="calendar"):
                pass

    with pytest.raises(TimeoutError):
        asyncio.run(body())
    saved = harness.plugin.grants.store.load("owner")
    assert saved is not None and saved.refresh_token == "refresh-2"


def test_cancelled_agrant_stays_cancelled_when_the_refresh_fails(harness: Harness) -> None:
    harness.store_sign_in("owner", "old", expires_in=10)
    harness.client.refresh_delay = 0.2
    harness.client.refresh_error = server_error()

    async def one() -> None:
        async with agrant(tool_name="calendar"):
            pass

    async def body() -> None:
        task = asyncio.create_task(one())
        while not harness.client.refresh_calls:
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(body())


def _on_daemon_thread(fn: Any, timeout: float = 5) -> Any:
    """``fn()`` on a daemon thread, failing rather than hanging the run."""
    out: dict[str, Any] = {}

    def target() -> None:
        try:
            out["value"] = fn()
        except BaseException as exc:
            out["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    assert not thread.is_alive(), "deadlocked"
    if "error" in out:
        raise out["error"]
    return out["value"]


def test_sync_grant_on_the_loop_of_an_agrant_refresh_fails_fast(harness: Harness) -> None:
    harness.store_sign_in("owner", "old", expires_in=10)
    harness.client.refresh_delay = 0.3

    async def one() -> str:
        async with agrant(tool_name="calendar"):
            return get_access_context().access(RESOURCE).access_token

    async def body() -> tuple[Any, str]:
        task = asyncio.create_task(one())
        while not harness.client.refresh_calls:
            await asyncio.sleep(0.005)
        # A blocking grant() on the holder's own loop thread.
        with grant(tool_name="calendar") as access:
            error = access.get_resource_error(RESOURCE)
        return error, await task

    error, token = _on_daemon_thread(lambda: asyncio.run(body()))
    assert error["code"] == "refresh_failed"
    assert error["retryable"] is True
    assert token == f"obo-access-1-for-{RESOURCE}"
    assert len(harness.client.refresh_calls) == 1


def test_lock_raises_on_the_thread_whose_loop_holds_alock(harness: Harness) -> None:
    from keycardai.hermes.store import LockHeldByThisLoop

    store = harness.plugin.grants.store
    released = threading.Event()

    def other_thread_waits() -> None:
        with store.lock("owner"):
            released.set()

    async def body() -> None:
        async with store.alock("owner"):
            with pytest.raises(LockHeldByThisLoop):
                with store.lock("owner"):
                    pass
            waiter = threading.Thread(target=other_thread_waits, daemon=True)
            waiter.start()
            await asyncio.sleep(0.1)
            # Another thread still waits for the holder as before.
            assert not released.is_set()
        await asyncio.to_thread(waiter.join, 5)
        assert released.is_set()
        # Once released, this thread takes it normally.
        with store.lock("owner"):
            pass

    _on_daemon_thread(lambda: asyncio.run(body()))


def test_agrant_refresh_client_does_not_outlive_its_loop(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    import gc
    import weakref

    from keycardai.hermes import grants as grants_module

    closed: list[bool] = []
    stub = harness.client

    class LoopHoldingClient:
        """Holds the loop strongly once used, as the httpx transport does."""

        def __init__(self, issuer: str) -> None:
            self.loop: Any = None
            self.index = len(closed)
            closed.append(False)

        async def refresh_token_grant(self, **kwargs: Any) -> Any:
            self.loop = asyncio.get_running_loop()
            return await stub.refresh_token_grant(**kwargs)

        async def aclose(self) -> None:
            closed[self.index] = True

    monkeypatch.setattr(grants_module, "AsyncClient", LoopHoldingClient)
    harness.plugin.grants.public_client = None
    loops: list[weakref.ref[asyncio.AbstractEventLoop]] = []

    async def body() -> str:
        loops.append(weakref.ref(asyncio.get_running_loop()))
        async with agrant(tool_name="calendar"):
            return get_access_context().access(RESOURCE).access_token

    for _ in range(2):
        harness.store_sign_in("owner", "old", expires_in=10)
        assert asyncio.run(body()).startswith("obo-access-")
    gc.collect()
    assert closed == [True, True]
    assert [ref() for ref in loops] == [None, None]
    assert harness.plugin.grants._engine_public is None


# row 21
@pytest.mark.parametrize("error", [network_error(), server_error()])
def test_row_21_other_refresh_failures_are_per_resource_and_leave_the_store(harness: Harness, error) -> None:
    harness.store_sign_in("owner", "old", expires_in=10)
    before = harness.plugin.grants.store.path("owner").read_text()
    harness.client.refresh_error = error
    assert harness.call("calendar", tool_reading()) == "RESOURCE_ERROR:refresh_failed"
    assert harness.plugin.grants.store.path("owner").read_text() == before
    assert harness.client.exchange_calls == []


# row 24
def test_row_24_impersonate_without_an_actors_entry(make_harness) -> None:
    h = make_harness(base_settings(default_pattern="impersonate", actors={"alice": "user-alice"}))
    tokens = FakeSession.bind(HERMES_SESSION_ACTOR_ID="alice")
    try:
        assert h.call("calendar", tool_reading()) == f"imp-user-alice-for-{RESOURCE}"
    finally:
        FakeSession.unbind(tokens)
    calls: list[str] = []
    result = h.call("calendar", ran(calls))
    assert calls == [] and "reason: missing_identity" in result
    assert len(h.client.impersonate_calls) == 1 and h.client.exchange_calls == []


# row 26
@pytest.mark.parametrize(
    ("settings", "named"),
    [
        (base_settings(application_credential={"type": "client_secret", "client_id": "a"}), "application_credential.secret_name"),
        (base_settings(application_credential={"type": "nope"}), "application_credential.type"),
        ({**base_settings(), "sign_in_resource": None}, "sign_in_resource"),
    ],
)
def test_row_26_misconfiguration_names_the_setting_and_registers_nothing(tmp_path, settings, named) -> None:
    from keycardai.hermes.plugin import register

    from .hermes_fakes import FakePluginContext

    ctx = FakePluginContext(settings)
    with pytest.raises(HermesConfigurationError) as info:
        register(ctx, isolation_mode=lambda: "inline", hermes_home=lambda: tmp_path, get_secret=lambda n: "s")
    assert f"plugins.entries.{PLUGIN_ID}.settings.{named}" in str(info.value)
    assert not ctx.registered_anything


def test_row_26_missing_secret_names_the_setting(tmp_path) -> None:
    from keycardai.hermes.plugin import register

    from .hermes_fakes import FakePluginContext

    ctx = FakePluginContext(base_settings())
    with pytest.raises(HermesConfigurationError, match="application_credential.secret_name"):
        register(ctx, isolation_mode=lambda: "inline", hermes_home=lambda: tmp_path, get_secret=lambda n: None)
    assert not ctx.registered_anything


# row 27
def test_row_27_empty_actor_is_owner_actor(harness: Harness) -> None:
    harness.store_sign_in("owner", "owner-token")
    tokens = FakeSession.bind(HERMES_SESSION_ACTOR_ID="")
    try:
        assert harness.call("calendar", tool_reading()) == f"obo-owner-token-for-{RESOURCE}"
    finally:
        FakeSession.unbind(tokens)


# row 38
def test_row_38_empty_actor_on_a_chat_turn_never_falls_back_to_the_owner(harness: Harness) -> None:
    # Hermes clears the actor when another sender's message joins a running
    # turn (gateway/session_context.py:224-225); that turn is not the owner's.
    harness.store_sign_in("owner", "owner-token")
    calls: list[str] = []
    tokens = FakeSession.bind(HERMES_SESSION_ACTOR_ID="", HERMES_SESSION_PLATFORM="discord")
    try:
        result = harness.call("calendar", ran(calls))
    finally:
        FakeSession.unbind(tokens)
    assert calls == []
    assert "reason: missing_identity" in result and "--actor" not in result
    assert harness.client.exchange_calls == []


# row 44
def test_row_44_empty_actor_on_an_api_server_turn_acts_as_the_owner(harness: Harness) -> None:
    # api_server binds a platform but no sender (gateway/platforms/api_server.py:3557)
    # and is not a messaging surface (gateway/session_context.py:241-244).
    harness.store_sign_in("owner", "owner-token")
    tokens = FakeSession.bind(HERMES_SESSION_ACTOR_ID="", HERMES_SESSION_PLATFORM="api_server")
    try:
        assert harness.call("calendar", tool_reading()) == f"obo-owner-token-for-{RESOURCE}"
    finally:
        FakeSession.unbind(tokens)


# row 45
def test_row_45_empty_actor_on_an_unknown_surface_is_missing_identity(harness: Harness) -> None:
    # Hermes counts any surface it does not list as messaging, so the plugin fails closed.
    harness.store_sign_in("owner", "owner-token")
    calls: list[str] = []
    tokens = FakeSession.bind(HERMES_SESSION_ACTOR_ID="", HERMES_SESSION_PLATFORM="brand-new-chat")
    try:
        result = harness.call("calendar", ran(calls))
    finally:
        FakeSession.unbind(tokens)
    assert calls == [] and "reason: missing_identity" in result
    assert harness.client.exchange_calls == []


# row 28
def test_row_28_actor_aliases_map_an_a2a_peer(make_harness) -> None:
    h = make_harness(base_settings(actor_aliases={"athena": "owner"}))
    h.store_sign_in("owner", "owner-token")
    tokens = FakeSession.bind(HERMES_SESSION_ACTOR_ID="athena")
    try:
        assert h.call("calendar", tool_reading()) == f"obo-owner-token-for-{RESOURCE}"
    finally:
        FakeSession.unbind(tokens)


# row 29
def test_row_29_tool_with_no_resources_runs_with_an_empty_context(harness: Harness) -> None:
    def tool(args: dict[str, Any]) -> str:
        access = get_access_context()
        return "EMPTY" if not access.has_errors() and not access.get_successful_resources() else "FULL"

    assert harness.call("terminal", tool) == "EMPTY"
    assert harness.client.exchange_calls == []


# row 35
def test_row_35_resources_cover_built_in_tools(make_harness) -> None:
    h = make_harness(base_settings(resources=[RESOURCE]))
    calls: list[str] = []
    result = h.call("terminal", ran(calls))
    assert calls == [] and result.startswith("sign_in_required: the tool terminal")


def test_middleware_resets_the_context_after_the_tool_raises(harness: Harness) -> None:
    harness.store_sign_in("owner")

    def boom(args: dict[str, Any]) -> str:
        raise ValueError("tool failed")

    with pytest.raises(ValueError):
        harness.call("calendar", boom)
    with pytest.raises(RuntimeError):
        get_access_context()


def test_registrations_match_the_spec_table(harness: Harness) -> None:
    assert list(harness.ctx.middleware) == ["tool_execution"]
    assert harness.ctx.secret_sources[0].name == "keycard"
    assert list(harness.ctx.cli_commands) == ["keycard"]
    assert list(harness.ctx.hooks) == []


@pytest.mark.skipif(sys.platform == "win32" or os.name != "posix", reason="flock")
def test_lock_file_is_private(harness: Harness) -> None:
    store = harness.plugin.grants.store
    with store.lock("owner"):
        pass
    assert stat.S_IMODE(store.path("owner").with_suffix(".lock").stat().st_mode) == 0o600
