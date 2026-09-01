"""Inbound authentication for an agent served by LangGraph.

The middleware in this package grants outbound access for a run. This module
covers the other half: who the run is for. It verifies the caller's own
zone-issued bearer on every request, hands the run that identity plus the raw
bearer, and scopes threads, runs and store items to the caller who created
them. One deployment then serves many callers, each under their own delegation
chain, instead of acting as whoever signed in last.

    from langgraph_sdk import Auth
    from keycardai.langchain.auth import (
        install_owner_authorization,
        zone_authenticator,
    )

    auth = Auth()
    auth.authenticate(
        zone_authenticator(
            zone_url="https://your-zone.keycard.cloud",
            resource="https://your-agent.example",
        )
    )
    install_owner_authorization(auth)

Point `langgraph.json` at that object and set `disable_studio_auth` to true;
see the package README for why that flag is not optional.

Importing this module needs the `serve` extra (`langgraph-sdk` and
`starlette`), so it is not re-exported from the package root.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any

from langgraph_sdk import Auth
from langgraph_sdk.auth import is_studio_user
from starlette.exceptions import HTTPException

from .middleware import OWNER_KEY, SUBJECT_TOKEN_FIELD

__all__ = [
    "VerifiedCaller",
    "VerifyToken",
    "install_owner_authorization",
    "zone_authenticator",
]


@dataclass(frozen=True)
class VerifiedCaller:
    """The result of verifying one inbound bearer."""

    identity: str
    scopes: tuple[str, ...] = ()


VerifyToken = Callable[[str], Awaitable[VerifiedCaller]]

_OWNER_SEGMENT_LENGTH = 16


def _metadata_url(zone_url: str) -> str:
    """RFC 8414 metadata for the zone that issues the accepted bearers.

    A challenged client reads it to find where to sign in.
    """
    return f"{zone_url.rstrip('/')}/.well-known/oauth-authorization-server"


def _challenge(
    metadata_url: str, status_code: int, error: str, description: str
) -> HTTPException:
    """A bearer challenge that survives the response.

    Starlette's exception, never `Auth.exceptions.HTTPException`: the SDK path
    drops response headers and coerces statuses, and a challenge needs both.
    """
    return HTTPException(
        status_code=status_code,
        detail=description,
        headers={
            "WWW-Authenticate": (
                f'Bearer error="{error}", error_description="{description}", '
                f'authorization_uri="{metadata_url}"'
            )
        },
    )


def _bearer(headers: Mapping[bytes, bytes] | None) -> str | None:
    raw = (headers or {}).get(b"authorization")
    if raw is None:
        return None
    scheme, _, token = raw.decode("latin-1").strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _zone_verify(zone_url: str, resource: str) -> VerifyToken:
    """Verify against the zone's JWKS, audienced at this agent's resource."""
    from keycardai.oauth.server.verifier import TokenVerifier
    from keycardai.oauth.utils.jwt import get_claims

    verifier = TokenVerifier(issuer=zone_url, audience=resource)

    async def verify(token: str) -> VerifiedCaller:
        access = await verifier.verify_token(token)
        # verify_token already checked this exact token's signature, issuer,
        # audience and expiry, so its claims are trustworthy here.
        claims = get_claims(token)
        identity = claims.get("email") or claims.get("sub")
        if not identity:
            raise ValueError("verified token carries neither email nor sub")
        return VerifiedCaller(identity=str(identity), scopes=tuple(access.scopes))

    return verify


def zone_authenticator(
    *,
    zone_url: str,
    resource: str,
    verify: VerifyToken | None = None,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Build the `@auth.authenticate` hook that verifies the caller's bearer.

    The returned hook reads the `Authorization` header, verifies the bearer as
    a zone-issued JWT, and returns the verified identity together with the raw
    bearer under `subject_token`. LangGraph delivers that dict to the run as
    `config["configurable"]["langgraph_auth_user"]`, which is the only
    per-request channel the middleware can read the caller's token from
    (`identity_source="auth_user"`).

    Every rejection, including an unexpected failure inside verification, is a
    401 carrying a `WWW-Authenticate: Bearer` challenge that names the zone's
    metadata URL, so a client learns where to sign in.

    Args:
        zone_url: Keycard zone URL. The bearer's issuer, and the base of the
            metadata URL named in the challenge.
        resource: This agent's resource URL, the audience the bearer must
            carry. A token minted for another resource is rejected.
        verify: Injectable verification seam. Left unset, tokens are verified
            against the zone's JWKS. Tests pass a stub so the suite needs no
            zone and no network.
    """
    if not zone_url:
        raise ValueError("zone_authenticator requires a zone_url")
    if not resource:
        raise ValueError("zone_authenticator requires a resource (the token audience)")
    # Zone tokens carry no trailing slash in their issuer, and the verifier
    # matches issuers exactly, so a slash on the configured zone_url would
    # reject every token while the challenge names a correct-looking URL.
    zone_url = zone_url.rstrip("/")
    metadata_url = _metadata_url(zone_url)
    verifier: VerifyToken | None = verify

    async def authenticate(headers: dict[bytes, bytes]) -> dict[str, Any]:
        token = _bearer(headers)
        if token is None:
            raise _challenge(
                metadata_url,
                401,
                "invalid_request",
                "A zone-issued bearer token is required",
            )
        nonlocal verifier
        if verifier is None:
            verifier = _zone_verify(zone_url, resource)
        try:
            caller = await verifier(token)
        except Exception as exc:
            raise _challenge(
                metadata_url,
                401,
                "invalid_token",
                f"Bearer token verification failed: {type(exc).__name__}",
            ) from exc
        return {
            "identity": caller.identity,
            "display_name": caller.identity,
            "permissions": list(caller.scopes),
            # The middleware exchanges this per tool call, under the caller's
            # own delegation chain. Nothing else carries it into the run.
            SUBJECT_TOKEN_FIELD: token,
        }

    return authenticate


def _owner(ctx: Auth.types.AuthContext) -> str:
    """The identity that owns whatever this request creates or reads."""
    if is_studio_user(ctx.user):
        raise HTTPException(status_code=403, detail="Studio users are not accepted")
    identity = ctx.user.identity
    if not identity:
        raise HTTPException(
            status_code=403, detail="Authenticated identity is required"
        )
    return identity


def _owner_segment(identity: str) -> str:
    """A store-safe owner segment.

    Identities are usually emails, and the store rejects namespace labels
    containing periods, so the raw identity cannot be a label. A digest is
    dot-free, fixed-shape, and collision-safe where naive character
    replacement is not: `a.b@x` and `a_b@x` must not share a namespace.
    """
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:_OWNER_SEGMENT_LENGTH]


def install_owner_authorization(auth: Auth) -> Auth:
    """Scope threads, runs and store items to the caller who created them.

    Authentication says who is calling; it grants no ownership by itself, so
    without these handlers any valid caller can read and resume any other
    caller's thread. Installs, on the passed `Auth` object:

    - owner metadata stamped on thread, run and store writes, taken from the
      verified identity and never from the request body,
    - reads, updates, searches and deletes filtered by that owner,
    - store namespaces prefixed with a digest of the owner, injected even when
      the request carries no namespace at all, so a prefix-less
      `list_namespaces` cannot enumerate other callers,
    - assistant reads and searches left open to any authenticated caller, so a
      chat client can fetch the graph schema, while creating or mutating an
      assistant is denied,
    - Studio users denied,
    - a catch-all denying every unmatched resource and action pair, because
      the framework otherwise fails open.

    Returns the same `Auth` object, so the call can be chained.
    """

    async def stamp_thread_owner(
        ctx: Auth.types.AuthContext, value: Auth.types.on.threads.create.value
    ) -> dict[str, str]:
        return _stamp(ctx, value)

    async def stamp_run_owner(
        ctx: Auth.types.AuthContext, value: Auth.types.on.threads.create_run.value
    ) -> dict[str, str]:
        """Runs and resumes: this owner filter is what stops a cross-owner resume."""
        return _stamp(ctx, value)

    async def own_threads_only(
        ctx: Auth.types.AuthContext, value: MutableMapping[str, Any]
    ) -> dict[str, str]:
        """Thread and run reads, updates, searches and deletes, scoped to the owner."""
        return {OWNER_KEY: _owner(ctx)}

    async def own_store_namespace_only(
        ctx: Auth.types.AuthContext, value: MutableMapping[str, Any]
    ) -> None:
        owner_segment = _owner_segment(_owner(ctx))
        for key in ("namespace", "namespace_prefix"):
            if key in value:
                value[key] = (owner_segment, *(value.get(key) or ()))
        if "namespace" not in value and "namespace_prefix" not in value:
            value["namespace"] = (owner_segment,)
        return None

    async def read_assistants(
        ctx: Auth.types.AuthContext, value: MutableMapping[str, Any]
    ) -> None:
        """Assistants are the deployment's static graph config, not caller data."""
        _owner(ctx)
        return None

    async def search_assistants(
        ctx: Auth.types.AuthContext, value: MutableMapping[str, Any]
    ) -> None:
        _owner(ctx)
        return None

    async def deny_unmatched(
        ctx: Auth.types.AuthContext, value: MutableMapping[str, Any]
    ) -> bool:
        return False

    auth.on.threads.create(stamp_thread_owner)
    auth.on.threads.create_run(stamp_run_owner)
    auth.on.threads(own_threads_only)
    auth.on.store(own_store_namespace_only)
    auth.on.assistants.read(read_assistants)
    auth.on.assistants.search(search_assistants)
    auth.on(deny_unmatched)
    return auth


def _stamp(
    ctx: Auth.types.AuthContext, value: MutableMapping[str, Any]
) -> dict[str, str]:
    """Record the owner on the resource being created, and filter on it."""
    owner = _owner(ctx)
    metadata = value.get("metadata")
    if metadata is None:
        metadata = {}
        value["metadata"] = metadata
    metadata[OWNER_KEY] = owner
    return {OWNER_KEY: owner}
