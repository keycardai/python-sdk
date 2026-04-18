"""Keycard OAuth Server Primitives.

Framework-free server components for protecting any HTTP API with Keycard.
These components depend only on pydantic, httpx, authlib, and cryptography —
no MCP, Starlette, or other framework dependencies.

Core Components:
    AccessContext: Non-throwing token access with per-resource error tracking
    TokenVerifier: JWT verification with JWKS caching and multi-zone support
    AccessToken: Verified access token model

Credential Providers:
    ApplicationCredential: Protocol for credential providers
    ClientSecret: Client credentials (BasicAuth) for token exchange
    WebIdentity: Private key JWT client assertion (RFC 7523)
    EKSWorkloadIdentity: EKS workload identity with mounted tokens

Infrastructure:
    ClientFactory, DefaultClientFactory: OAuth client creation
    JWKSCache, JWKSKey: JWKS key caching
    PrivateKeyManager, FilePrivateKeyStorage: Private key management
"""

from .access_context import AccessContext
from .credentials import (
    ApplicationCredential,
    ClientSecret,
    EKSWorkloadIdentity,
    WebIdentity,
)
from .token_exchange import exchange_tokens_for_resources
from .verifier import AccessToken, TokenVerifier

__all__ = [
    # === Core ===
    "AccessContext",
    "AccessToken",
    "TokenVerifier",
    # === Token Exchange ===
    "exchange_tokens_for_resources",
    # === Credential Providers ===
    "ApplicationCredential",
    "ClientSecret",
    "EKSWorkloadIdentity",
    "WebIdentity",
]
