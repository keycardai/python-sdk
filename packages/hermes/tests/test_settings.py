"""Settings validation, the workload identity credential, and the Hermes default seams."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from keycardai.hermes import middleware, plugin, secret_source
from keycardai.hermes.settings import (
    HermesConfigurationError,
    build_credential,
    load_settings,
    settings_from_mapping,
)

from .conftest import base_settings


@pytest.mark.parametrize(
    ("overrides", "key"),
    [
        ({"zone_url": None}, "zone_url"),
        ({"zone_url": 3}, "zone_url"),
        ({"resources": [1]}, "resources"),
        ({"actors": {"a": 1}}, "actors"),
        ({"block_without_sign_in": "yes"}, "block_without_sign_in"),
        ({"default_pattern": "sideways"}, "default_pattern"),
        ({"tool_resources": "x"}, "tool_resources"),
        ({"tool_resources": {"t": [1]}}, "tool_resources.t"),
        ({"application_credential": "x"}, "application_credential"),
        ({"application_credential": {"type": "client_secret", "client_id": 1, "secret_name": "S"}}, "application_credential.client_id"),
        ({"application_credential": {"type": "workload_identity", "client_id": 1}}, "application_credential.client_id"),
        ({"application_credential": {"type": "workload_identity", "token_file": 1}}, "application_credential.token_file"),
    ],
)
def test_invalid_settings_name_the_key(overrides, key) -> None:
    with pytest.raises(HermesConfigurationError) as info:
        settings_from_mapping(base_settings(**overrides))
    assert info.value.key.endswith(key)


def test_string_shorthands_and_defaults() -> None:
    settings = settings_from_mapping(base_settings(resources="https://one", tool_resources={"t": "https://two"}))
    assert settings.resources == ["https://one"]
    assert settings.tool_resources == {"t": ["https://two"]}
    assert settings.block_without_sign_in is True and settings.verify_sign_in is False
    assert settings.default_pattern == "on_behalf_of"


def test_as_self_needs_no_sign_in_resource() -> None:
    settings = base_settings(default_pattern="as_self")
    del settings["sign_in_resource"]
    assert settings_from_mapping(settings).sign_in_resource is None


def test_workload_identity_credential(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("jwt")
    settings = settings_from_mapping(
        base_settings(application_credential={"type": "workload_identity", "client_id": "app", "token_file": str(token_file)})
    )
    credential = build_credential(settings, lambda name: None)
    assert credential.client_id == "app"


def test_workload_identity_without_a_file_names_the_setting(tmp_path: Path, monkeypatch) -> None:
    for name in ("KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE", "AWS_WEB_IDENTITY_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    settings = settings_from_mapping(base_settings(application_credential={"type": "workload_identity"}))
    with pytest.raises(HermesConfigurationError, match="application_credential.token_file"):
        build_credential(settings, lambda name: None)


def test_load_settings_reads_through_get_config() -> None:
    reads: list[str] = []

    def get(key: str, default=None):
        reads.append(key)
        return base_settings().get(key, default)

    assert load_settings(get).zone_url == base_settings()["zone_url"]
    assert "zone_url" in reads and "application_credential" in reads


def _install(monkeypatch, name: str, **attrs) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


def test_default_seams_use_hermes_when_importable(monkeypatch, tmp_path: Path) -> None:
    _install(monkeypatch, "gateway.session_context", get_session_env=lambda name, default="": f"{name}!")
    _install(monkeypatch, "tools.registry", registry=types.SimpleNamespace(get_toolset_for_tool=lambda n: "mcp-x"))
    _install(monkeypatch, "agent.secret_scope", get_secret=lambda name: "from-scope")
    _install(monkeypatch, "hermes_cli.plugin_isolation", isolation_mode=lambda: "host")
    _install(monkeypatch, "hermes_constants", get_hermes_home=lambda: str(tmp_path))
    _install(monkeypatch, "agent.secret_sources.base", FetchResult=lambda **f: ("fetch-result", f))
    assert middleware.default_session_env("HERMES_SESSION_ACTOR_ID") == "HERMES_SESSION_ACTOR_ID!"
    assert middleware.default_toolset_of("t") == "mcp-x"
    assert plugin.default_get_secret("S") == "from-scope"
    assert plugin.default_isolation_mode() == "host"
    assert plugin.default_hermes_home() == tmp_path
    assert secret_source._result(secrets={"a": "b"})[0] == "fetch-result"


def test_default_seams_fall_back_without_hermes(monkeypatch, tmp_path: Path) -> None:
    for name in ("gateway.session_context", "tools.registry", "agent.secret_scope", "hermes_cli.plugin_isolation", "hermes_constants"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setenv("HERMES_SESSION_ACTOR_ID", "env-actor")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("S", "from-env")
    assert middleware.default_session_env("HERMES_SESSION_ACTOR_ID") == "env-actor"
    assert middleware.default_toolset_of("t") is None
    assert plugin.default_get_secret("S") == "from-env"
    assert plugin.default_isolation_mode() == "inline"
    assert plugin.default_hermes_home() == tmp_path
    monkeypatch.setenv(plugin.HOST_PROCESS_ENV, "1")
    assert plugin.default_isolation_mode() == "host"


def test_grant_without_a_registered_plugin() -> None:
    plugin._registered = None
    with pytest.raises(RuntimeError, match="not registered"):
        plugin.current_plugin()
