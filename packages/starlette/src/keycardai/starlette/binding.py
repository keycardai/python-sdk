"""Configuration shared by token verification, metadata and challenges."""

import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from re import fullmatch
from types import MappingProxyType
from typing import Literal
from urllib.parse import SplitResult, urlsplit

from pydantic import AnyHttpUrl

from keycardai.oauth.server.exceptions import VerifierConfigError

from .shared.zone import is_valid_zone_id

Audience = str | list[str] | dict[str, str] | None
_Mode = Literal["legacy", "single", "per_zone", "multi", "shared", "any"]
PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"


class ResourceBindingConfigError(VerifierConfigError):
    """Invalid resource binding or incompatible verifier configuration."""


class LegacyResourceBindingWarning(UserWarning):
    """Resource server configuration without an explicit audience mode."""


def _validate_identifier(identifier: str) -> None:
    if (
        not isinstance(identifier, str)
        or not identifier
        or any(char.isspace() or ord(char) < 32 for char in identifier)
    ):
        raise ResourceBindingConfigError("Invalid resource identifier")
    if fullmatch(
        r"(?i:urn):[a-zA-Z0-9][a-zA-Z0-9-]{0,30}[a-zA-Z0-9]:[^#]+(?:#.*)?", identifier
    ):
        return
    url = http_identifier(identifier)
    if url is not None and not identifier.isascii():
        raise ResourceBindingConfigError(
            "HTTP(S) resource identifiers must use ASCII; encode the host with IDNA "
            "and percent-encode non-ASCII path and query characters"
        )
    try:
        if (
            url is not None
            and url.hostname
            and "@" not in url.netloc
            and ("#" not in identifier and not any(c in identifier for c in '\\"'))
        ):
            _ = url.port
            AnyHttpUrl(identifier)
            return
    except ValueError:
        pass
    raise ResourceBindingConfigError("Expected an absolute HTTP(S) URL or URN")


def http_identifier(identifier: str) -> SplitResult | None:
    try:
        url = urlsplit(identifier)
    except ValueError:
        return None
    return url if url.scheme in {"http", "https"} else None


def metadata_url(identifier: str) -> str:
    scheme, _, _ = identifier.partition("://")
    url = urlsplit(identifier)
    path = url.path if url.path != "/" else ""
    query = f"?{url.query}" if "?" in identifier else ""
    return f"{scheme}://{url.netloc}{PROTECTED_RESOURCE_PATH}{path}{query}"


def zone_issuer(base_url: str, zone_id: str) -> str:
    url = AnyHttpUrl(base_url)
    default_port = 443 if url.scheme == "https" else 80
    port = f":{url.port}" if url.port and url.port != default_port else ""
    return f"{url.scheme}://{zone_id}.{url.host}{port}"


@dataclass(frozen=True, init=False)
class ResourceBinding:
    """An explicit resource-server audience and discovery policy."""

    mode: _Mode
    resource_identifier: str | None
    resource_identifiers_by_zone: Mapping[str, str]
    _audience: str | tuple[str, ...] | Mapping[str, str] | None

    def __init__(
        self,
        *,
        resource_identifier: str | None = None,
        resource_identifiers_by_zone: Mapping[str, str] | None = None,
        multi_resource: bool = False,
        audience: Audience = None,
        allow_any_audience: bool = False,
        _metadata_only: bool = False,
    ) -> None:
        single = resource_identifier is not None
        zoned = resource_identifiers_by_zone is not None
        if (single and (zoned or multi_resource or allow_any_audience)) or (
            allow_any_audience and (zoned or multi_resource)
        ):
            raise ResourceBindingConfigError("Conflicting resource binding options")
        if resource_identifier is not None:
            _validate_identifier(resource_identifier)
        identifiers = dict(resource_identifiers_by_zone or {})
        if zoned and not identifiers:
            raise ResourceBindingConfigError("resource_identifiers_by_zone is empty")
        for zone_id, identifier in identifiers.items():
            if not is_valid_zone_id(zone_id):
                raise ResourceBindingConfigError("Invalid zone ID")
            _validate_identifier(identifier)
        if isinstance(audience, list) and not audience:
            raise ResourceBindingConfigError("audience must not be an empty list")
        if audience is not None and (
            single or allow_any_audience or (zoned and not multi_resource)
        ):
            raise ResourceBindingConfigError(
                "audience conflicts with the selected mode"
            )

        mode: _Mode
        if multi_resource:
            if zoned:
                mode = "shared"
                resource_identifier = next(iter(identifiers.values()))
                if http_identifier(resource_identifier) is None or any(
                    value != resource_identifier for value in identifiers.values()
                ):
                    raise ResourceBindingConfigError(
                        "Shared discovery requires one identical HTTP(S) identifier"
                    )
                if _metadata_only and audience is None:
                    audience = [resource_identifier]
                if audience != [resource_identifier]:
                    raise ResourceBindingConfigError(
                        "Shared discovery requires a singleton audience list "
                        "containing the shared identifier"
                    )
            else:
                mode = "multi"
                if not isinstance(audience, list) and not (
                    _metadata_only and audience is None
                ):
                    raise ResourceBindingConfigError(
                        "multi_resource requires a nonempty audience list"
                    )
                if isinstance(audience, list) and not all(
                    isinstance(value, str) for value in audience
                ):
                    raise ResourceBindingConfigError(
                        "multi_resource requires string audience values"
                    )
        elif zoned:
            mode = "per_zone"
            audience = identifiers
        elif single:
            mode = "single"
            audience = resource_identifier
        elif allow_any_audience:
            mode = "any"
        else:
            mode = "legacy"
            replacement = (
                "resource_identifier or resource_identifiers_by_zone"
                if isinstance(audience, dict)
                else "resource_identifier"
            )
            warnings.warn(
                f"Legacy resource binding is deprecated; configure {replacement} "
                "or select an explicit audience mode.",
                LegacyResourceBindingWarning,
                stacklevel=3,
            )
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "resource_identifier", resource_identifier)
        object.__setattr__(
            self, "resource_identifiers_by_zone", MappingProxyType(identifiers)
        )
        policy = (
            tuple(audience)
            if isinstance(audience, list)
            else MappingProxyType(dict(audience))
            if isinstance(audience, dict)
            else audience
        )
        object.__setattr__(self, "_audience", policy)

    @property
    def audience(self) -> Audience:
        """The corresponding policy for a TokenVerifier."""
        if isinstance(self._audience, tuple):
            return list(self._audience)
        if isinstance(self._audience, Mapping):
            return dict(self._audience)
        return self._audience

    def identifier_for(self, zone_id: str | None) -> str | None:
        if self.mode == "per_zone":
            return self.resource_identifiers_by_zone.get(zone_id) if zone_id else None
        return self.resource_identifier

    def _validate_multi_zone(self, enable_multi_zone: bool) -> None:
        if enable_multi_zone and self.mode == "single" and self.metadata_for(None):
            raise ResourceBindingConfigError(
                "enable_multi_zone requires resource_identifiers_by_zone "
                "for an HTTP(S) resource_identifier"
            )

    def metadata_for(self, zone_id: str | None) -> str | None:
        identifier = self.identifier_for(zone_id)
        return (
            metadata_url(identifier)
            if identifier is not None and http_identifier(identifier) is not None
            else None
        )

    def challenge_metadata(
        self, path: bytes, query: bytes, zone_id: str | None
    ) -> str | None:
        identifier = self.identifier_for(zone_id)
        if identifier is None:
            return None
        url = http_identifier(identifier)
        if url is not None and (
            path == (url.path or "/").encode() and query == url.query.encode()
        ):
            return metadata_url(identifier)
        return None

    def zone_issuers(self, issuer: str) -> dict[str, str]:
        if not issuer:
            if any(
                http_identifier(i) for i in self.resource_identifiers_by_zone.values()
            ):
                raise ResourceBindingConfigError("A base issuer is required")
            return {}
        try:
            return {
                z: zone_issuer(issuer, z) for z in self.resource_identifiers_by_zone
            }
        except ValueError as exc:
            raise ResourceBindingConfigError("Invalid base issuer") from exc

    def documents(self, issuer: str) -> list[tuple[str, list[str]]]:
        zones = self.zone_issuers(issuer)
        if self.mode == "per_zone":
            documents = [
                (identifier, [zones[zone]])
                for zone, identifier in self.resource_identifiers_by_zone.items()
                if self.metadata_for(zone) is not None
            ]
            keys = {
                (url.path, url.query)
                for identifier, _ in documents
                for url in (urlsplit(metadata_url(identifier)),)
            }
            if len(keys) != len(documents):
                raise ResourceBindingConfigError(
                    "Ambiguous protected-resource metadata URL"
                )
            return documents
        if self.resource_identifier is not None and self.metadata_for(None) is not None:
            return [
                (
                    self.resource_identifier,
                    list(zones.values()) if self.mode == "shared" else [issuer],
                )
            ]
        return []


def resolve_binding(
    resource_binding: ResourceBinding | None,
    *,
    resource_identifier: str | None,
    resource_identifiers_by_zone: Mapping[str, str] | None,
    multi_resource: bool,
    audience: Audience,
    allow_any_audience: bool,
    metadata_only: bool = False,
) -> ResourceBinding:
    if resource_binding is not None:
        if (
            resource_identifier is not None
            or resource_identifiers_by_zone is not None
            or multi_resource
            or audience is not None
            or allow_any_audience
        ):
            raise ResourceBindingConfigError(
                "Pass resource_binding or options, not both"
            )
        return resource_binding
    return ResourceBinding(
        resource_identifier=resource_identifier,
        resource_identifiers_by_zone=resource_identifiers_by_zone,
        multi_resource=multi_resource,
        audience=audience,
        allow_any_audience=allow_any_audience,
        _metadata_only=metadata_only,
    )
