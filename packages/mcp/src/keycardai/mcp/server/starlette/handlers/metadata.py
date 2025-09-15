import json
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from pydantic import AnyHttpUrl, Field
from starlette.requests import Request
from starlette.responses import Response

from mcp.shared.auth import ProtectedResourceMetadata


class InferredProtectedResourceMetadata(ProtectedResourceMetadata):
    """Extended ProtectedResourceMetadata that allows resource to be inferred from request."""
    resource: AnyHttpUrl | None = Field(default=None)  # Override to make it optional

@dataclass
class AuthorizationServerMetadata:
    base_url: str


def _is_authorization_server_zone_scoped(authorization_server_urls: AnyHttpUrl) -> bool:
    if len(authorization_server_urls) != 1:
        return False
    return len(authorization_server_urls[0].host.split(".")) == 3

def _get_zone_id_from_path(path: str) -> str | None:
    path = path.lstrip("/").rstrip("/")
    zone_id = path.split("/")[0]
    if zone_id == "" or zone_id == "/":
        return None
    return zone_id

def _remove_well_known_prefix(path: str) -> str:
    prefix = ".well-known/oauth-protected-resource"
    path = path.lstrip("/").rstrip("/")
    if path.startswith(prefix):
        return path[len(prefix):]
    return path

def _create_zone_scoped_authorization_server_url(zone_id: str, authorization_server_url: AnyHttpUrl) -> AnyHttpUrl:
    port_part = f":{authorization_server_url.port}" if authorization_server_url.port else ""
    url = f"{authorization_server_url.scheme}://{zone_id}.{authorization_server_url.host}{port_part}"
    return AnyHttpUrl(url)

def _strip_zone_id_from_path(zone_id: str, path: str) -> str:
    path = path.lstrip("/").rstrip("/")
    if path.startswith(zone_id):
        return path[len(zone_id):]
    return path

def _create_resource_url(base_url: str | AnyHttpUrl, path: str) -> AnyHttpUrl:
    base_url_str = str(base_url).rstrip("/")
    if path and not path.startswith("/"):
        path = "/" + path
    url = f"{base_url_str}{path}".rstrip("/")
    if url.endswith("://") or (path == "/" and not url.endswith("/")):
        url += "/"
    return AnyHttpUrl(url)

def _remove_authorization_server_prefix(path: str) -> str:
    """Remove the /.well-known/oauth-authorization-server prefix from the path."""
    auth_server_prefix = "/.well-known/oauth-authorization-server"
    if path.startswith(auth_server_prefix):
        return path[len(auth_server_prefix):]
    return path

def protected_resource_metadata(metadata: InferredProtectedResourceMetadata, enable_multi_zone: bool = False) -> Callable:
    def wrapper(request: Request) -> Response:
        # Create a copy of the metadata to avoid mutating the original
        request_metadata = metadata.model_copy(deep=True)
        path = _remove_well_known_prefix(request.url.path)
        if enable_multi_zone or not _is_authorization_server_zone_scoped(request_metadata.authorization_servers):
            zone_id = _get_zone_id_from_path(path)
            if zone_id:
                request_metadata.authorization_servers = [ _create_zone_scoped_authorization_server_url(zone_id, request_metadata.authorization_servers[0]) ]

        resource = _create_resource_url(request.base_url, path)
        mcp_version = request.headers.get("mcp-protocol-version")
        request_metadata.resource = resource
        # TODO: what is the reason for this?
        if mcp_version == "2025-03-26":
            json["authorization_servers"] = [ request.base_url ]
        return Response(content=request_metadata.model_dump_json(), status_code=200)
    return wrapper

def authorization_server_metadata(issuer: str, enable_multi_zone: bool = False) -> Callable:
    def wrapper(request: Request) -> Response:
        try:
            actual_issuer = issuer
            path = _remove_authorization_server_prefix(request.url.path)

            if enable_multi_zone or not _is_authorization_server_zone_scoped([AnyHttpUrl(issuer)]):
                zone_id = _get_zone_id_from_path(path)
                if zone_id:
                    actual_issuer = str(_create_zone_scoped_authorization_server_url(zone_id, AnyHttpUrl(issuer)))

            with httpx.Client() as client:
                resp = client.get(f"{actual_issuer}/.well-known/oauth-authorization-server")
                resp.raise_for_status()
                authorization_server_metadata = resp.json()
                authorization_server_metadata["authorization_endpoint"] = f"{request.base_url}{authorization_server_metadata['authorization_endpoint']}"
                return Response(content=json.dumps(authorization_server_metadata), status_code=200)
        except Exception as e:
            error_message = {"error": str(e), "type": type(e).__name__}
            return Response(content=json.dumps(error_message), status_code=500)
    return wrapper
