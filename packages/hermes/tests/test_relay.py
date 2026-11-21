"""Relay rows: 401 retry, 404 session replay, batches, notifications, SSE, missing secret."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
import yaml

from keycardai.hermes.relay import Relay, load_profile_settings, main
from keycardai.hermes.settings import PLUGIN_ID, settings_from_mapping
from keycardai.hermes.store import SignIn, SignInStore

from .conftest import SECRET_NAME, ZONE, base_settings
from .hermes_fakes import StubClient

URL = "https://mcp.example.test/mcp"


class FakeServer:
    """A streamable HTTP MCP server over httpx.MockTransport."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.unauthorized_remaining = 0
        self.lost_sessions: set[str] = set()
        self.sessions = 0
        self.sse_methods: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        if self.unauthorized_remaining:
            self.unauthorized_remaining -= 1
            return httpx.Response(401)
        session = request.headers.get("Mcp-Session-Id")
        if body.get("method") == "initialize":
            self.sessions += 1
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body["id"], "result": {"session": self.sessions}},
                headers={"Mcp-Session-Id": f"s{self.sessions}"},
            )
        if session in self.lost_sessions:
            return httpx.Response(404)
        if "id" not in body:
            return httpx.Response(202)
        if body.get("method") in self.sse_methods:
            text = (
                f'event: message\ndata: {json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"})}\n\n'
                f'data: {json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"sse": True}})}\n\n'
            )
            return httpx.Response(200, text=text, headers={"Content-Type": "text/event-stream"})
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"echo": body.get("method"), "session": session}}
        )


def make_relay(tmp_path: Path, server: FakeServer, *, secret: str | None = "s3cret", **overrides: Any) -> tuple[Relay, StubClient]:
    settings = settings_from_mapping(base_settings(**overrides))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    SignInStore(home, ZONE).save("owner", SignIn("public-1", "refresh-1", "owner-token", 4_000_000_000))
    client = StubClient()
    environ = {SECRET_NAME: secret} if secret is not None else {}
    relay = Relay(
        settings, URL, home=home, environ=environ, http=httpx.Client(transport=httpx.MockTransport(server.handler)), client=client
    )
    return relay, client


def run(relay: Relay, *messages: Any) -> list[dict[str, Any]]:
    inp = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    out = io.StringIO()
    relay.run(inp, out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def req(id: int, method: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": {}}


# row 30
def test_row_30_401_acquires_a_new_token_and_retries_once(tmp_path) -> None:
    server = FakeServer()
    relay, client = make_relay(tmp_path, server)
    server.unauthorized_remaining = 1
    assert run(relay, req(1, "tools/list"))[0]["result"]["echo"] == "tools/list"
    assert len(client.exchange_calls) == 2 and len(server.requests) == 2
    server.unauthorized_remaining = 2
    replies = run(relay, req(2, "tools/list"))
    assert replies[0]["error"]["code"] == -32603 and URL in replies[0]["error"]["message"]
    assert "obo-" not in replies[0]["error"]["message"]
    assert len(client.exchange_calls) == 3


# row 31
def test_row_31_404_replays_initialize_and_retries_on_the_new_session(tmp_path) -> None:
    server = FakeServer()
    relay, _ = make_relay(tmp_path, server)
    init = req(1, "initialize")
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert run(relay, init, initialized)[0]["result"] == {"session": 1}
    assert relay.session_id == "s1"
    server.lost_sessions.add("s1")
    replies = run(relay, req(2, "tools/list"))
    assert replies == [{"jsonrpc": "2.0", "id": 2, "result": {"echo": "tools/list", "session": "s2"}}]
    methods = [json.loads(r.content).get("method") for r in server.requests]
    assert methods == ["initialize", "notifications/initialized", "tools/list", "initialize", "notifications/initialized", "tools/list"]
    assert relay.session_id == "s2"


# row 32
def test_row_32_batch_notification_and_sse(tmp_path) -> None:
    server = FakeServer()
    server.sse_methods.add("tools/call")
    relay, _ = make_relay(tmp_path, server)
    batch = [req(1, "tools/list"), req(2, "prompts/list")]
    notification = {"jsonrpc": "2.0", "method": "notifications/cancelled"}
    replies = run(relay, batch, notification, req(3, "tools/call"))
    assert [r.get("id") for r in replies] == [1, 2, None, 3]
    assert replies[2] == {"jsonrpc": "2.0", "method": "notifications/progress"}
    assert replies[3]["result"] == {"sse": True}
    assert len(server.requests) == 4


# row 34
def test_row_34_missing_secret_env_fails_every_request_without_a_token_request(tmp_path) -> None:
    server = FakeServer()
    relay, client = make_relay(tmp_path, server, secret=None)
    replies = run(relay, req(1, "tools/list"), {"jsonrpc": "2.0", "method": "notifications/x"}, req(2, "tools/list"))
    assert [r["error"]["code"] for r in replies] == [-32603, -32603]
    assert all(URL in r["error"]["message"] for r in replies)
    assert client.exchange_calls == [] and client.self_calls == [] and server.requests == []


def test_relay_reuses_the_token_until_near_expiry(tmp_path) -> None:
    server = FakeServer()
    relay, client = make_relay(tmp_path, server)
    run(relay, req(1, "a"), req(2, "b"))
    assert len(client.exchange_calls) == 1
    relay._token_expires_at = 0
    run(relay, req(3, "c"))
    assert len(client.exchange_calls) == 2


def test_relay_resource_option_and_sign_in_required(tmp_path) -> None:
    server = FakeServer()
    settings = settings_from_mapping(base_settings())
    home = tmp_path / "empty"
    home.mkdir()
    relay = Relay(settings, URL, resource="https://gw.example.test", home=home, environ={SECRET_NAME: "s"},
                  http=httpx.Client(transport=httpx.MockTransport(server.handler)), client=StubClient())
    replies = run(relay, req(1, "tools/list"))
    assert replies[0]["error"]["code"] == -32603
    assert "https://gw.example.test" in replies[0]["error"]["message"]


def test_load_profile_settings_reads_the_plugin_block(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"entries": {PLUGIN_ID: {"settings": base_settings()}}}})
    )
    assert load_profile_settings(tmp_path).zone_url == ZONE


def test_main_reports_a_missing_config(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert main([URL]) == 2
    assert "keycardai-hermes-mcp" in capsys.readouterr().err
