"""OAuth metadata handlers.

Re-exported from keycardai.starlette.handlers.metadata for backward compatibility.
Canonical import: ``from keycardai.starlette.handlers.metadata import ...``
"""

from keycardai.starlette.handlers.metadata import (
    ProtectedResourceMetadata as InferredProtectedResourceMetadata,
    _create_jwks_uri,
    _create_resource_url,
    _create_zone_scoped_authorization_server_url,
    _get_zone_id_from_path,
    _remove_authorization_server_prefix,
    _remove_well_known_prefix,
    authorization_server_metadata,
    protected_resource_metadata,
)


# Not in starlette — was only in MCP's version. Provide it here for test compat.
def _is_authorization_server_zone_scoped(authorization_server_urls) -> bool:
    if len(authorization_server_urls) != 1:
        return False
    return len(authorization_server_urls[0].host.split(".")) == 3


def _strip_zone_id_from_path(zone_id: str, path: str) -> str:
    path = path.lstrip("/").rstrip("/")
    if path.startswith(zone_id):
        return path[len(zone_id):]
    return path


__all__ = [
    "InferredProtectedResourceMetadata",
    "authorization_server_metadata",
    "protected_resource_metadata",
    "_create_resource_url",
    "_create_zone_scoped_authorization_server_url",
    "_get_zone_id_from_path",
    "_remove_well_known_prefix",
    "_remove_authorization_server_prefix",
    "_create_jwks_uri",
    "_is_authorization_server_zone_scoped",
    "_strip_zone_id_from_path",
]
