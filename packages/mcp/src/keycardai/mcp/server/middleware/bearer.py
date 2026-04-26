"""Bearer token authentication middleware.

Re-exported from keycardai.starlette.middleware.bearer for backward compatibility.
Canonical import: ``from keycardai.starlette.middleware import BearerAuthMiddleware``
"""

from keycardai.starlette.middleware.bearer import (
    BearerAuthMiddleware,
    _get_bearer_token,
    _get_oauth_protected_resource_url,
)

__all__ = [
    "BearerAuthMiddleware",
    "_get_bearer_token",
    "_get_oauth_protected_resource_url",
]
