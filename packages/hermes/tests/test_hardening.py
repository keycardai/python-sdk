"""Store keys, profile lookup, host isolation, relay resilience, login locking,
UserInfo matching and secret source warnings."""

from __future__ import annotations

import io
import json
import stat
from pathlib import Path

import pytest

from keycardai.hermes import plugin as plugin_module
from keycardai.hermes.plugin import current_plugin, default_isolation_mode, grant
from keycardai.hermes.store import SignIn, SignInStore

from .conftest import RESOURCE, ZONE, base_settings
from .test_relay import FakeServer, make_relay, req, run


def test_distinct_actors_never_share_a_sign_in_file(tmp_path: Path) -> None:
    store = SignInStore(tmp_path, ZONE)
    actors = ["a@b.com", "a_b.com", "a b.com", "x" * 64 + "1", "x" * 64 + "2"]
    paths = {store.path(actor) for actor in actors}
    assert len(paths) == len(actors)
    store.save("a@b.com", SignIn("c", "r", "token-at", 4_000_000_000))
    assert store.load("a_b.com") is None


def test_store_directories_are_private(tmp_path: Path) -> None:
    store = SignInStore(tmp_path, ZONE)
    store.save("owner", SignIn("c", "r", "t", 4_000_000_000))
    assert stat.S_IMODE(store.dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.dir.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path("owner").stat().st_mode) == 0o600
    assert [p.name for p in store.dir.iterdir() if p.name.endswith(".tmp")] == []


def test_plugin_host_child_counts_as_host_isolation(monkeypatch) -> None:
    # Inside the host child Hermes's isolation_mode() answers in_process
    # (hermes_cli/plugin_isolation.py:101-104), so the env var decides.
    monkeypatch.setenv("HERMES_PLUGIN_HOST_PROCESS", "1")
    assert default_isolation_mode() == "host"


def test_grant_looks_up_the_profile_by_hermes_home(make_harness) -> None:
    first = make_harness(base_settings(owner_actor="alice"))
    second = make_harness(base_settings(owner_actor="bob"))
    first.store_sign_in("alice", "alice-token")
    second.store_sign_in("bob", "bob-token")

    def inside() -> bool:
        return True

    assert current_plugin(home=lambda: second.home, inside_hermes=inside) is second.plugin
    assert current_plugin(home=lambda: first.home, inside_hermes=inside) is first.plugin
    with pytest.raises(RuntimeError, match="not registered for this profile"):
        current_plugin(home=lambda: first.home.parent, inside_hermes=inside)


def test_inside_hermes_a_single_registration_is_not_lent_to_another_profile(harness, tmp_path: Path) -> None:
    other_home = tmp_path / "other-profile"
    other_home.mkdir()
    with pytest.raises(RuntimeError):
        current_plugin(home=lambda: other_home, inside_hermes=lambda: True)
    assert current_plugin(home=lambda: other_home, inside_hermes=lambda: False) is harness.plugin


def test_grant_with_two_profiles_uses_the_active_one(make_harness, monkeypatch) -> None:
    first = make_harness(base_settings(owner_actor="alice"))
    second = make_harness(base_settings(owner_actor="bob"))
    first.store_sign_in("alice", "alice-token")
    second.store_sign_in("bob", "bob-token")
    monkeypatch.setattr(plugin_module, "_inside_hermes", lambda: True)
    monkeypatch.setattr(plugin_module, "default_hermes_home", lambda: second.home)
    with grant(resources=[RESOURCE]) as access:
        assert access.access(RESOURCE).access_token == f"obo-bob-token-for-{RESOURCE}"
    monkeypatch.setattr(plugin_module, "default_hermes_home", lambda: first.home)
    with grant(resources=[RESOURCE]) as access:
        assert access.access(RESOURCE).access_token == f"obo-alice-token-for-{RESOURCE}"


def test_relay_keeps_running_after_an_unexpected_error(tmp_path: Path) -> None:
    server = FakeServer()
    relay, _ = make_relay(tmp_path, server)
    store = relay.grants.store if relay.grants else None
    assert store is not None
    store.path("owner").write_text("{not json")
    replies = run(relay, req(1, "tools/list"))
    assert replies[0]["error"]["code"] == -32603
    assert "JSONDecodeError" in replies[0]["error"]["message"]
    store.save("owner", SignIn("public-1", "refresh-1", "owner-token", 4_000_000_000))
    replies = run(relay, req(2, "tools/list"))
    assert replies[0]["result"]["echo"] == "tools/list"


def test_relay_answers_a_parse_error_with_id_null(tmp_path: Path) -> None:
    server = FakeServer()
    relay, _ = make_relay(tmp_path, server)
    out = io.StringIO()
    relay.run(io.StringIO("{oops\n" + json.dumps(req(3, "ping")) + "\n"), out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert lines[0] == {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "keycard relay: parse error"}}
    assert lines[1]["id"] == 3


def test_login_saves_under_the_sign_in_lock(harness, monkeypatch) -> None:
    store = harness.plugin.grants.store
    events: list[str] = []
    real_lock, real_save = store.lock, store.save

    def lock(actor: str):
        events.append(f"lock:{actor}")
        return real_lock(actor)

    def save(actor: str, sign_in: SignIn) -> None:
        events.append(f"save:{actor}")
        real_save(actor, sign_in)

    monkeypatch.setattr(store, "lock", lock)
    monkeypatch.setattr(store, "save", save)
    assert harness.cli("login", "--actor", "A")[0] == 0
    assert events == ["lock:A", "save:A"]


def test_verify_sign_in_matches_an_email_identifier(make_harness) -> None:
    h = make_harness(base_settings(verify_sign_in=True, actors={"A": "Larry@Example.com"}))
    h.client.userinfo_email = "larry@example.com"
    assert h.cli("login", "--actor", "A")[0] == 0
    assert h.plugin.grants.store.load("A") is not None


def test_verify_sign_in_rejects_an_unverified_email(make_harness) -> None:
    h = make_harness(base_settings(verify_sign_in=True, actors={"A": "larry@example.com"}))
    h.client.userinfo_email = "larry@example.com"
    h.client.userinfo_email_verified = False
    assert h.cli("login", "--actor", "A")[0] == 1
    assert h.plugin.grants.store.load("A") is None


def test_verify_sign_in_does_not_match_an_email_against_a_non_email(make_harness) -> None:
    h = make_harness(base_settings(verify_sign_in=True, actors={"A": "user-expected"}))
    h.client.userinfo_email = "user-expected"
    assert h.cli("login", "--actor", "A")[0] == 1
    assert h.plugin.grants.store.load("A") is None


def test_login_without_sign_in_resource_names_the_setting(harness) -> None:
    from keycardai.hermes.cli import LoginError, login

    object.__setattr__(harness.plugin.settings, "sign_in_resource", None)
    with pytest.raises(LoginError) as info:
        login(harness.plugin, "A", client=harness.client)
    assert info.value.code == "missing_sign_in_resource"


def test_secret_source_warns_for_a_name_it_could_not_acquire(make_harness) -> None:
    h = make_harness(base_settings(secrets={"CAL_KEY": RESOURCE}))
    h.client.self_granted = False
    result = h.ctx.secret_sources[0].fetch({}, h.home)
    assert result.secrets == {}
    assert len(result.warnings) == 1
    assert "CAL_KEY" in result.warnings[0] and RESOURCE in result.warnings[0]
    assert "self-for" not in result.warnings[0]


def test_login_command_shell_quotes_the_actor() -> None:
    from keycardai.hermes.grants import login_command, sign_in_text

    assert login_command("a b;$(x)") == "hermes keycard login --actor 'a b;$(x)'"
    assert "--actor 'a b;$(x)'" in sign_in_text("missing_identity", "a b;$(x)", "t")


def test_refresh_failure_is_not_a_consent_prompt(make_harness) -> None:
    from .conftest import base_settings
    from .hermes_fakes import server_error

    h = make_harness(base_settings(authorization_url="https://consent.example.test"))
    h.store_sign_in("owner", "old", expires_in=10)
    h.client.refresh_error = server_error()
    ran: list[str] = []

    def tool(args):
        ran.append("ran")
        return "ok"

    assert h.call("calendar", tool) == "ok" and ran == ["ran"]


def test_toolset_lookup_failure_counts_as_not_mcp(monkeypatch) -> None:
    import sys
    import types

    from keycardai.hermes import middleware

    def boom(name):
        raise KeyError(name)

    module = types.ModuleType("tools.registry")
    module.registry = types.SimpleNamespace(get_toolset_for_tool=boom)
    monkeypatch.setitem(sys.modules, "tools.registry", module)
    assert middleware.default_toolset_of("t") is None


def test_admission_hook_without_a_sender_lets_the_turn_run(make_harness) -> None:
    import types

    from .conftest import base_settings

    h = make_harness(base_settings(sign_in_on_admission=True))
    hook = h.ctx.hooks["post_gateway_admission"][0]
    assert hook(session_key="k", platform="telegram", source=None, message_id="1", text="hi") is None
    assert hook(session_key="k", platform="telegram", source={}, message_id="1", text="hi") is None
    obj = types.SimpleNamespace(user_id="carol")
    result = hook(session_key="k", platform="telegram", source=obj, message_id="1", text="hi")
    assert result is not None and "--actor carol" in result["reply"]
