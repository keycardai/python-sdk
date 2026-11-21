"""``keycardai-hermes-mcp``: a stdio MCP relay to a streamable HTTP server."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

import httpx
import yaml

from .grants import Grants, resolve_actor
from .plugin import build_grants
from .settings import (
    PLUGIN_ID,
    HermesConfigurationError,
    HermesSettings,
    settings_from_mapping,
)
from .store import EXPIRY_SKEW_SECONDS

INTERNAL_ERROR = -32603
SESSION_HEADER = "Mcp-Session-Id"


def _one_line(text: str) -> str:
    """A reply as one line of stdout: pretty-printed JSON is compacted; text
    that is not JSON is passed through with its newlines removed."""
    try:
        return json.dumps(json.loads(text), separators=(",", ":"))
    except json.JSONDecodeError:
        return " ".join(text.split())


class RelayError(Exception):
    """A request could not be relayed; the message names the resource, never a token."""


def load_profile_settings(home: Path) -> HermesSettings:
    """The plugin's settings block from the profile's ``config.yaml``."""
    config = yaml.safe_load((home / "config.yaml").read_text()) or {}
    block: Any = config
    for key in ("plugins", "entries", PLUGIN_ID, "settings"):
        block = block.get(key) if isinstance(block, Mapping) else None
    if not isinstance(block, Mapping):
        raise HermesConfigurationError("", "no settings block in config.yaml")
    return settings_from_mapping(block)


class Relay:
    """Relays each JSON-RPC message from standard input to ``url`` with a token
    for ``resource`` acquired as ``owner_actor`` under ``default_pattern``.

    The relay runs outside Hermes, so the ``client_secret`` form of
    ``application_credential`` is read from the environment variable named by
    its secret name; with it unset every request gets a JSON-RPC error and no
    token request is sent.
    """

    def __init__(
        self,
        settings: HermesSettings,
        url: str,
        *,
        resource: str | None = None,
        home: Path,
        environ: Mapping[str, str],
        http: httpx.Client | None = None,
        client: Any = None,
    ) -> None:
        self.settings = settings
        self.url = url
        self.resource = resource or url
        self.http = http or httpx.Client(timeout=60.0)
        self.grants: Grants | None
        self.credential_error: str | None
        try:
            self.grants = build_grants(settings, home=home, get_secret=environ.get, client=client)
            self.credential_error = None
        except HermesConfigurationError as exc:
            self.grants = None
            self.credential_error = str(exc)
        self.session_id: str | None = None
        self.initialize_request: dict[str, Any] | None = None
        self.initialized_notification: dict[str, Any] | None = None
        self._token: str | None = None
        self._token_expires_at = 0.0

    # Token

    def token(self) -> str:
        if self._token and time.time() < self._token_expires_at - EXPIRY_SKEW_SECONDS:
            return self._token
        if self.grants is None:
            raise RelayError(f"no credential for {self.resource}: {self.credential_error}")
        actor = resolve_actor(self.settings, None)
        access, needed = self.grants.acquire(actor, [self.resource])
        if needed is not None or access.has_error():
            error = access.get_error() or {}
            raise RelayError(f"no token for {self.resource}: {error.get('message', needed.reason if needed else 'error')}")
        if access.has_resource_error(self.resource):
            error = access.get_resource_error(self.resource) or {}
            raise RelayError(f"no token for {self.resource}: {error.get('message', 'error')}")
        response = access.access(self.resource)
        self._token = response.access_token
        expires_in = response.expires_in if response.expires_in is not None else 300
        self._token_expires_at = time.time() + float(expires_in)
        return self._token

    def invalidate_token(self) -> None:
        self._token = None
        self._token_expires_at = 0.0

    # Transport

    def _post(self, message: dict[str, Any]) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.token()}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session_id:
            headers[SESSION_HEADER] = self.session_id
        return self.http.post(self.url, content=json.dumps(message).encode(), headers=headers)

    def _replay_session(self) -> None:
        if self.initialize_request is None:
            raise RelayError(f"the session with {self.resource} is gone and no initialize was seen")
        self.session_id = None
        response = self._post(self.initialize_request)
        if response.status_code >= 400:
            raise RelayError(f"re-initialize with {self.resource} failed with HTTP {response.status_code}")
        self.session_id = response.headers.get(SESSION_HEADER) or None
        if self.initialized_notification is not None:
            self._post(self.initialized_notification)

    def send(self, message: dict[str, Any], *, retry_auth: bool = True, retry_session: bool = True) -> list[str]:
        """Relay one message; the replies as JSON text, one per line to write."""
        response = self._post(message)
        if response.status_code == 401 and retry_auth:
            self.invalidate_token()
            return self.send(message, retry_auth=False, retry_session=retry_session)
        if response.status_code == 404 and self.session_id and retry_session:
            self._replay_session()
            return self.send(message, retry_auth=retry_auth, retry_session=False)
        if response.status_code >= 400:
            raise RelayError(f"{self.resource} answered HTTP {response.status_code}")
        if message.get("method") == "initialize":
            self.session_id = response.headers.get(SESSION_HEADER) or self.session_id
        if response.status_code == 202 or not response.content:
            return []
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/event-stream"):
            return [
                _one_line(line[len("data:"):].strip())
                for line in response.text.splitlines()
                if line.startswith("data:") and line[len("data:"):].strip()
            ]
        return [_one_line(response.text.strip())]

    # Loop

    def handle(self, message: dict[str, Any], out: IO[str]) -> None:
        method = message.get("method")
        if method == "initialize":
            self.initialize_request = message
        elif method == "notifications/initialized":
            self.initialized_notification = message
        is_request = "id" in message
        try:
            replies = self.send(message)
        except (RelayError, httpx.HTTPError) as exc:
            if is_request:
                self._write(out, json.dumps({
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": INTERNAL_ERROR, "message": f"keycard relay: {exc}"},
                }))
            return
        for reply in replies:
            self._write(out, reply)

    @staticmethod
    def _write(out: IO[str], text: str) -> None:
        out.write(text + "\n")
        out.flush()

    def run(self, inp: IO[str], out: IO[str]) -> int:
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            for message in parsed if isinstance(parsed, list) else [parsed]:
                if isinstance(message, dict):
                    self.handle(message, out)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="keycardai-hermes-mcp", description=__doc__)
    parser.add_argument("url", help="The streamable HTTP MCP endpoint")
    parser.add_argument("--resource", default=None, help="The resource to acquire a token for (default: the URL)")
    args = parser.parse_args(argv)
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    try:
        settings = load_profile_settings(home)
    except (OSError, HermesConfigurationError, yaml.YAMLError) as exc:
        print(f"keycardai-hermes-mcp: {exc}", file=sys.stderr)
        return 2
    relay = Relay(settings, args.url, resource=args.resource, home=home, environ=os.environ)
    return relay.run(sys.stdin, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
