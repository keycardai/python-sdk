"""OAuth 2.0 client module.

Simple client structure with clean imports.
"""

from .auth import (
    ClientCredentialsAuth,
    ClientSecretBasic,
    JWTAuth,
    MTLSAuth,
    NoneAuth,
)
from .client import AsyncClient, Client
from .http import AsyncHTTPClient, HTTPClient, HTTPClientProtocol

__all__ = [
    "AsyncClient",
    "Client",
    "ClientCredentialsAuth",
    "ClientSecretBasic",
    "JWTAuth",
    "MTLSAuth",
    "NoneAuth",
    "AsyncHTTPClient",
    "HTTPClient",
    "HTTPClientProtocol",
]
