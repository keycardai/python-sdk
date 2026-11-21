from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from keycardai.hermes import plugin as plugin_module
from keycardai.hermes.plugin import KeycardPlugin, register

from .hermes_fakes import FakePluginContext, FakeSession, StubClient

ZONE = "https://zone.example.test"
RESOURCE = "https://api.example.test"
OTHER = "https://other.example.test"
SIGN_IN_RESOURCE = "urn:hermes:assistant"
SECRET_NAME = "KEYCARD_CLIENT_SECRET"


def base_settings(**overrides: Any) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "zone_url": ZONE,
        "application_credential": {
            "type": "client_secret",
            "client_id": "app-1",
            "secret_name": SECRET_NAME,
        },
        "sign_in_resource": SIGN_IN_RESOURCE,
        "tool_resources": {"calendar": [RESOURCE], "two": [RESOURCE, OTHER]},
        "owner_actor": "owner",
    }
    settings.update(overrides)
    return settings


class Harness:
    """One registered plugin over a fake context, a stub client and a tmp Hermes home."""

    def __init__(self, tmp_path: Path, settings: dict[str, Any], **register_kwargs: Any) -> None:
        self.home = tmp_path / f"hermes-home-{id(self)}"
        self.home.mkdir()
        self.ctx = FakePluginContext(settings)
        self.client = register_kwargs.pop("client", StubClient())
        self.session = register_kwargs.pop("session", FakeSession())
        self.toolsets: dict[str, str] = register_kwargs.pop("toolsets", {})
        self.secrets: dict[str, str] = register_kwargs.pop("secrets", {SECRET_NAME: "s3cret"})
        self.failures: list[BaseException] = []
        kwargs: dict[str, Any] = {
            "isolation_mode": lambda: "inline",
            "get_secret": self.secrets.get,
            "hermes_home": lambda: self.home,
            "session_env": self.session.get_session_env,
            "toolset_of": self.toolsets.get,
            "client": self.client,
        }
        kwargs.update(register_kwargs)
        self.plugin: KeycardPlugin = register(self.ctx, **kwargs)
        self.now = 1_700_000_000.0
        self.plugin.grants.clock = lambda: self.now

    @property
    def middleware(self) -> list[Any]:
        return self.ctx.middleware["tool_execution"]

    def call(self, tool_name: str, tool: Any, args: dict[str, Any] | None = None) -> Any:
        from .hermes_fakes import run_tool_execution_middleware

        return run_tool_execution_middleware(
            self.middleware, tool_name, args or {}, tool, failures=self.failures
        )

    def store_sign_in(self, actor: str, access_token: str = "subject-1", *, expires_in: float = 3600,
                      refresh_token: str | None = "refresh-1", client_id: str = "public-1") -> None:
        from keycardai.hermes.store import SignIn

        self.plugin.grants.store.save(
            actor, SignIn(client_id, refresh_token, access_token, self.now + expires_in)
        )

    def cli(self, *argv: str, client: Any = None) -> tuple[int, str, str]:
        import contextlib
        import io

        from keycardai.hermes.cli import handle

        parser = argparse.ArgumentParser(prog="hermes keycard")
        self.plugin.setup_cli(parser)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = handle(self.plugin, parser.parse_args(argv), client=client or self.client)
        return code, out.getvalue(), err.getvalue()


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path, base_settings())


@pytest.fixture
def make_harness(tmp_path: Path):
    def make(settings: dict[str, Any] | None = None, **kwargs: Any) -> Harness:
        return Harness(tmp_path, settings if settings is not None else base_settings(), **kwargs)

    return make


@pytest.fixture(autouse=True)
def _reset_registered():
    yield
    plugin_module._registered.clear()
