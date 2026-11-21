"""Fakes mirroring Hermes Agent v0.23.2 (NousResearch/hermes-agent @ ae2a81cc).

PluginContext: register_middleware(kind, callback) (hermes_cli/plugins_context.py:757),
register_secret_source(source) (:874), register_cli_command(name, help, setup_fn,
handler_fn=None, description="") (:444), register_hook(hook_name, callback, *, hold_stream=False)
(:753), get_config(key, default) (:75). Middleware chain: run_tool_execution_middleware(tool_name,
args, next_call, **context) (hermes_cli/middleware.py:144); a raising callback is reported and the
rest of the chain, the tool included, still runs (:203-213). Session vars:
get_session_env(name, default="") reads a per-turn ContextVar (gateway/session_context.py:230).
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

from keycardai.oauth import TokenResponse
from keycardai.oauth.exceptions import NetworkError, RefreshGrantError
from keycardai.oauth.types.models import (
    ClientRegistrationResponse,
    DeviceAuthorization,
    TokenExchangeRequest,
    UserInfoResponse,
)


def jwt_with_exp(exp: float) -> str:
    def b64(part: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(part).encode())
        return raw.rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64({'exp': exp})}.sig"


class FakePluginContext:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.middleware: dict[str, list[Callable]] = {}
        self.secret_sources: list[Any] = []
        self.cli_commands: dict[str, dict[str, Any]] = {}
        self.hooks: dict[str, list[Callable]] = {}

    def get_config(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def register_middleware(self, kind: str, callback: Callable) -> None:
        self.middleware.setdefault(kind, []).append(callback)

    def register_secret_source(self, source: Any) -> None:
        self.secret_sources.append(source)

    def register_cli_command(
        self, name: str, help: str, setup_fn: Callable, handler_fn: Callable | None = None,
        description: str = "",
    ) -> None:
        self.cli_commands[name] = {"help": help, "setup_fn": setup_fn, "handler_fn": handler_fn}

    def register_hook(self, hook_name: str, callback: Callable, *, hold_stream: bool = False) -> None:
        self.hooks.setdefault(hook_name, []).append(callback)

    @property
    def registered_anything(self) -> bool:
        return bool(self.middleware or self.secret_sources or self.cli_commands or self.hooks)


def run_tool_execution_middleware(
    callbacks: list[Callable], tool_name: str, args: dict[str, Any], next_call: Callable, **context: Any
) -> Any:
    """Hermes's chain, with its skip-and-continue on a raising callback."""
    failures: list[BaseException] = context.pop("failures", [])

    def call_at(index: int, payload: Any) -> Any:
        if index >= len(callbacks):
            return next_call(payload)
        callback = callbacks[index]
        state = {"called": False}

        def chained(next_payload: Any = None) -> Any:
            state["called"] = True
            return call_at(index + 1, payload if next_payload is None else next_payload)

        try:
            return callback(tool_name=tool_name, args=payload, next_call=chained, **context)
        except Exception as exc:
            failures.append(exc)
            if state["called"]:
                raise
            return call_at(index + 1, payload)

    return call_at(0, args)


_SESSION: dict[str, ContextVar[str | None]] = {
    "HERMES_SESSION_ACTOR_ID": ContextVar("fake_actor_id", default=None),
    "HERMES_SESSION_USER_ID": ContextVar("fake_user_id", default=None),
}


class FakeSession:
    """Per-turn session vars, and a record of which names were read."""

    def __init__(self) -> None:
        self.reads: list[str] = []

    def get_session_env(self, name: str, default: str = "") -> str:
        self.reads.append(name)
        var = _SESSION.get(name)
        if var is None:
            return default
        value = var.get()
        return default if value is None else value

    @staticmethod
    def bind(**values: str | None) -> list[Any]:
        return [_SESSION[name].set(value) for name, value in values.items()]

    @staticmethod
    def unbind(tokens: list[Any]) -> None:
        for name, token in zip(_SESSION, tokens, strict=False):
            _SESSION[name].reset(token)


class StubClient:
    """Stands in for keycardai.oauth.AsyncClient on every path the plugin uses."""

    def __init__(self) -> None:
        self.exchange_calls: list[TokenExchangeRequest] = []
        self.impersonate_calls: list[dict[str, Any]] = []
        self.self_calls: list[dict[str, Any]] = []
        self.refresh_calls: list[dict[str, Any]] = []
        self.register_calls: list[Any] = []
        self.device_calls: list[dict[str, Any]] = []
        self.poll_calls: list[dict[str, Any]] = []
        self.userinfo_calls: list[str] = []
        self.denied_resources: set[str] = set()
        self.self_granted = True
        self.refresh_error: Exception | None = None
        self.refresh_rotates_to: str | None = "refresh-2"
        self.refresh_delay = 0.0
        self.device_refresh_token: str | None = "refresh-1"
        self.userinfo_sub = "user-1"
        self.exchange_raises: Exception | None = None

    async def exchange_token(self, request: TokenExchangeRequest) -> TokenResponse:
        self.exchange_calls.append(request)
        if self.exchange_raises is not None:
            raise self.exchange_raises
        if request.resource in self.denied_resources:
            raise RuntimeError("no grant for this resource yet")
        return TokenResponse(
            access_token=f"obo-{request.subject_token}-for-{request.resource}",
            token_type="Bearer",
            expires_in=300,
        )

    async def impersonate(self, *, user_identifier: str, resource: str, scope: str | None = None) -> TokenResponse:
        self.impersonate_calls.append({"user": user_identifier, "resource": resource})
        return TokenResponse(access_token=f"imp-{user_identifier}-for-{resource}", token_type="Bearer")

    async def client_credentials_grant(self, request=None, **kwargs: Any) -> TokenResponse:
        self.self_calls.append(kwargs)
        if not self.self_granted:
            raise RuntimeError("policy denies this application self access")
        return TokenResponse(access_token=f"self-for-{kwargs.get('resource')}", token_type="Bearer", expires_in=300)

    async def refresh_token_grant(self, **kwargs: Any) -> TokenResponse:
        self.refresh_calls.append(kwargs)
        if self.refresh_delay:
            import asyncio

            await asyncio.sleep(self.refresh_delay)
        if self.refresh_error is not None:
            raise self.refresh_error
        return TokenResponse(
            access_token=f"access-{len(self.refresh_calls)}",
            token_type="Bearer",
            expires_in=600,
            refresh_token=self.refresh_rotates_to,
        )

    async def register_client(self, request: Any = None, **kwargs: Any) -> ClientRegistrationResponse:
        self.register_calls.append(request)
        return ClientRegistrationResponse(client_id="public-1")

    async def request_device_authorization(self, **kwargs: Any) -> DeviceAuthorization:
        self.device_calls.append(kwargs)
        return DeviceAuthorization(
            device_code="dev",
            user_code="ABCD",
            verification_uri="https://zone/device",
            expires_in=600,
            expires_at=datetime.now(UTC) + timedelta(seconds=600),
            interval=0,
        )

    async def wait_for_device_token(self, authorization: DeviceAuthorization, **kwargs: Any) -> TokenResponse:
        self.poll_calls.append(kwargs)
        return TokenResponse(
            access_token="device-access", token_type="Bearer", expires_in=600, refresh_token=self.device_refresh_token
        )

    async def userinfo(self, access_token: str, **kwargs: Any) -> UserInfoResponse:
        self.userinfo_calls.append(access_token)
        return UserInfoResponse(sub=self.userinfo_sub, claims={"sub": self.userinfo_sub})


def invalid_grant() -> RefreshGrantError:
    return RefreshGrantError("invalid_grant", "refresh token revoked", status_code=400)


def server_error() -> RefreshGrantError:
    return RefreshGrantError("temporarily_unavailable", "try later", status_code=503)


def network_error() -> NetworkError:
    return NetworkError(ConnectionError("connection reset"))


NOW = time.time
