from starlette.routing import Mount, Route

from ..handlers.metadata import (
    InferredProtectedResourceMetadata,
    authorization_server_metadata,
    protected_resource_metadata,
)


def auth_metadata_mount(issuer: str, enable_multi_zone: bool = False) -> Mount:
    return Mount(
        path="/.well-known",
        routes=[
            Route(
                "/oauth-protected-resource{resource_path:path}",
                protected_resource_metadata(
                    InferredProtectedResourceMetadata(
                        authorization_servers=[issuer],
                    ),
                    enable_multi_zone=enable_multi_zone
                ),
                name="oauth-protected-resource"
            ),
            Route(
                "/oauth-authorization-server{resource_path:path}",
                authorization_server_metadata(issuer, enable_multi_zone=enable_multi_zone),
                name="oauth-authorization-server"
            )
        ],
        name="well-known",
    )
