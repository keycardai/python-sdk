"""A Hermes secret source serving the names in ``secrets``, acquired as-itself."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .grants import Grants

SOURCE_NAME = "keycard"


def _result(**fields: Any) -> Any:
    """Hermes's ``FetchResult`` when Hermes is importable, else a stand-in with the same fields."""
    try:
        FetchResult = importlib.import_module("agent.secret_sources.base").FetchResult
    except ImportError:
        defaults: dict[str, Any] = {
            "secrets": {}, "applied": [], "skipped": [], "warnings": [], "error": None, "error_kind": None,
        }
        return SimpleNamespace(**{**defaults, **fields})
    return FetchResult(**fields)


class KeycardSecretSource:
    """Static keys for a profile: each name in ``secrets`` is a token for its
    resource, acquired as-itself under ``application_credential``.

    Hermes hydrates secret sources once per process per Hermes home and never
    asks again, and this source acquires once too: a second ``fetch`` serves
    the first result. Anything that rotates goes through the middleware, the
    relay or ``hermes keycard token``.
    """

    api_version = 1
    name = SOURCE_NAME
    label = "Keycard"
    shape = "mapped"
    scheme = None
    token_env_key = None
    default_token_env = ""
    override_existing_default = False
    remediation_hints: dict[Any, str] = {}

    def __init__(self, grants: Grants) -> None:
        self.grants = grants
        self._fetched: dict[str, str] | None = None
        self.acquisitions = 0

    def is_enabled(self, cfg: dict[str, Any]) -> bool:
        return bool(self.grants.settings.secrets)

    def override_existing(self, cfg: dict[str, Any]) -> bool:
        return False

    def token_env(self, cfg: dict[str, Any]) -> str:
        return ""

    def protected_env_vars(self, cfg: dict[str, Any]) -> frozenset[str]:
        return frozenset()

    def fetch_timeout_seconds(self, cfg: dict[str, Any]) -> float:
        return 120.0

    def config_schema(self) -> dict[str, Any]:
        return {}

    def remediation(self, kind: Any, cfg: dict[str, Any]) -> str:
        return "Check the profile's application_credential and the resource of each name in secrets."

    def fetch(self, cfg: dict[str, Any], home_path: Path) -> Any:
        if self._fetched is None:
            self._fetched = self._acquire()
        return _result(secrets=dict(self._fetched))

    def _acquire(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for name, resource in self.grants.settings.secrets.items():
            self.acquisitions += 1
            access, _ = self.grants.acquire(None, [resource], "as_self")
            if resource in access.get_successful_resources():
                values[name] = access.access(resource).access_token
        return values
