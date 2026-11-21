"""Plugin settings: ``plugins.entries.keycardai-hermes.settings`` in a profile's config."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from keycardai.oauth.server import (
    ApplicationCredential,
    ClientSecret,
    FileTokenSource,
    WorkloadIdentity,
)
from keycardai.oauth.server.exceptions import WorkloadIdentityConfigurationError

PLUGIN_ID = "keycardai-hermes"
SETTINGS_PATH = f"plugins.entries.{PLUGIN_ID}.settings"
PATTERNS = ("on_behalf_of", "impersonate", "as_self")


class HermesConfigurationError(ValueError):
    """A setting is missing or malformed; the message names it."""

    def __init__(self, key: str, problem: str, *, absolute: bool = False) -> None:
        self.key = key if absolute else f"{SETTINGS_PATH}.{key}"
        super().__init__(f"{self.key}: {problem}")


@dataclass(frozen=True)
class CredentialSettings:
    """The ``application_credential`` block.

    ``client_secret``: ``{type: client_secret, client_id: ..., secret_name: ...}``;
    the secret value is read through ``get_secret`` (Hermes) or the environment
    variable of that name (relay), never from the settings file.
    ``workload_identity``: ``{type: workload_identity, client_id?: ..., token_file?: ...}``
    read through ``FileTokenSource``.
    """

    type: str
    client_id: str | None = None
    secret_name: str | None = None
    token_file: str | None = None


@dataclass(frozen=True)
class HermesSettings:
    zone_url: str
    application_credential: CredentialSettings
    sign_in_resource: str | None = None
    resources: list[str] = field(default_factory=list)
    tool_resources: dict[str, list[str]] = field(default_factory=dict)
    request_scopes: Any = None
    default_pattern: str = "on_behalf_of"
    owner_actor: str | None = None
    actor_aliases: dict[str, str] = field(default_factory=dict)
    actors: dict[str, str] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    block_without_sign_in: bool = True
    sign_in_on_admission: bool = False
    authorization_url: str | None = None
    verify_sign_in: bool = False


Getter = Callable[[str, Any], Any]


def _str(get: Getter, key: str, *, required: bool = False) -> str | None:
    value = get(key, None)
    if value is None or value == "":
        if required:
            raise HermesConfigurationError(key, "is required")
        return None
    if not isinstance(value, str):
        raise HermesConfigurationError(key, "must be a string")
    return value


def _str_list(get: Getter, key: str) -> list[str]:
    value = get(key, None)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise HermesConfigurationError(key, "must be a list of URIs")


def _str_map(get: Getter, key: str) -> dict[str, str]:
    value = get(key, None)
    if value is None:
        return {}
    if isinstance(value, Mapping) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        return dict(value)
    raise HermesConfigurationError(key, "must be a map of strings")


def _bool(get: Getter, key: str, default: bool) -> bool:
    value = get(key, None)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise HermesConfigurationError(key, "must be true or false")


def _credential(get: Getter) -> CredentialSettings:
    key = "application_credential"
    block = get(key, None)
    if not isinstance(block, Mapping):
        raise HermesConfigurationError(
            key, "is required and must be a client_secret or workload_identity block"
        )
    kind = block.get("type")
    if kind == "client_secret":
        client_id = block.get("client_id")
        secret_name = block.get("secret_name")
        if not isinstance(client_id, str) or not client_id:
            raise HermesConfigurationError(f"{key}.client_id", "is required")
        if not isinstance(secret_name, str) or not secret_name:
            raise HermesConfigurationError(f"{key}.secret_name", "is required")
        return CredentialSettings("client_secret", client_id, secret_name)
    if kind == "workload_identity":
        client_id = block.get("client_id")
        token_file = block.get("token_file")
        if client_id is not None and not isinstance(client_id, str):
            raise HermesConfigurationError(f"{key}.client_id", "must be a string")
        if token_file is not None and not isinstance(token_file, str):
            raise HermesConfigurationError(f"{key}.token_file", "must be a path")
        return CredentialSettings("workload_identity", client_id, None, token_file)
    raise HermesConfigurationError(
        f"{key}.type", "must be client_secret or workload_identity"
    )


def load_settings(get: Getter) -> HermesSettings:
    """Build settings from a ``get(key, default)`` reader such as ``ctx.get_config``."""
    pattern = _str(get, "default_pattern") or "on_behalf_of"
    if pattern not in PATTERNS:
        raise HermesConfigurationError(
            "default_pattern", f"must be one of {', '.join(PATTERNS)}"
        )
    tool_resources_raw = get("tool_resources", None) or {}
    if not isinstance(tool_resources_raw, Mapping):
        raise HermesConfigurationError("tool_resources", "must map tool names to URIs")
    tool_resources: dict[str, list[str]] = {}
    for name, uris in tool_resources_raw.items():
        if isinstance(uris, str):
            uris = [uris]
        if not isinstance(uris, list) or not all(isinstance(u, str) for u in uris):
            raise HermesConfigurationError(
                f"tool_resources.{name}", "must be a list of URIs"
            )
        tool_resources[str(name)] = list(uris)
    settings = HermesSettings(
        zone_url=_str(get, "zone_url", required=True) or "",
        application_credential=_credential(get),
        sign_in_resource=_str(get, "sign_in_resource"),
        resources=_str_list(get, "resources"),
        tool_resources=tool_resources,
        request_scopes=get("request_scopes", None),
        default_pattern=pattern,
        owner_actor=_str(get, "owner_actor"),
        actor_aliases=_str_map(get, "actor_aliases"),
        actors=_str_map(get, "actors"),
        secrets=_str_map(get, "secrets"),
        block_without_sign_in=_bool(get, "block_without_sign_in", True),
        sign_in_on_admission=_bool(get, "sign_in_on_admission", False),
        authorization_url=_str(get, "authorization_url"),
        verify_sign_in=_bool(get, "verify_sign_in", False),
    )
    if settings.default_pattern == "on_behalf_of" and not settings.sign_in_resource:
        raise HermesConfigurationError(
            "sign_in_resource", "is required when default_pattern is on_behalf_of"
        )
    return settings


def settings_from_mapping(block: Mapping[str, Any]) -> HermesSettings:
    """Settings from the raw block, as the relay reads it from ``config.yaml``."""
    return load_settings(lambda key, default: block.get(key, default))


def build_credential(
    settings: HermesSettings, get_secret: Callable[[str], str | None]
) -> ApplicationCredential:
    """The ``ApplicationCredential`` for ``application_credential``.

    Raises ``HermesConfigurationError`` naming the setting when the secret is
    missing or the workload identity source is misconfigured.
    """
    cred = settings.application_credential
    if cred.type == "client_secret":
        assert cred.client_id is not None and cred.secret_name is not None
        secret = get_secret(cred.secret_name)
        if not secret:
            raise HermesConfigurationError(
                "application_credential.secret_name",
                f"secret {cred.secret_name!r} is not available",
            )
        return ClientSecret((cred.client_id, secret))
    try:
        source = FileTokenSource(cred.token_file)
        return WorkloadIdentity(source, client_id=cred.client_id)
    except WorkloadIdentityConfigurationError as exc:
        raise HermesConfigurationError(
            "application_credential.token_file", str(exc)
        ) from exc
