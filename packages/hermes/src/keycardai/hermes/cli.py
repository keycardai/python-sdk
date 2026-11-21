"""``hermes keycard login`` and ``hermes keycard token``."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING, Any

from keycardai.oauth import AsyncClient
from keycardai.oauth.types.models import ClientRegistrationRequest
from keycardai.oauth.types.oauth import GrantType, TokenEndpointAuthMethod

from .grants import resolve_actor
from .settings import PLUGIN_ID
from .store import SignIn

if TYPE_CHECKING:
    from .plugin import KeycardPlugin


class LoginError(Exception):
    """``login`` stored nothing; ``code`` is ``refresh_token_missing``,
    ``sign_in_mismatch`` or ``missing_actor``."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def setup_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="keycard_command", required=True)
    login = sub.add_parser("login", help="Sign in an actor with the device flow")
    login.add_argument("--actor", default=None, help="The actor to store the sign-in under (default: owner_actor)")
    token = sub.add_parser("token", help="Write a token for a resource to standard output")
    token.add_argument("resource", help="The resource URI")
    group = token.add_mutually_exclusive_group()
    group.add_argument("--actor", default=None, help="Acquire for this actor (default: owner_actor)")
    group.add_argument("--as-self", action="store_true", help="Acquire as the application itself")


def handle(plugin: KeycardPlugin, args: argparse.Namespace, *, client: Any = None) -> int:
    """Run a ``hermes keycard`` subcommand; the exit code is the return value."""
    try:
        if args.keycard_command == "login":
            actor = login(plugin, args.actor, client=client)
            print(f"Signed in as actor {actor}.", file=sys.stderr)
            return 0
        if args.keycard_command == "token":
            print(token(plugin, args.resource, actor=args.actor, as_self=args.as_self))
            return 0
    except Exception as exc:
        print(f"keycard: {exc}", file=sys.stderr)
        return 1
    print(f"keycard: unknown command {args.keycard_command!r}", file=sys.stderr)
    return 2


def login(plugin: KeycardPlugin, actor: str | None, *, client: Any = None) -> str:
    """The device flow as the profile's public client, stored under ``actor``
    (default ``owner_actor``, after ``actor_aliases``, so the middleware's lookup
    finds it). Registers the client on first use in the zone.
    The Python Client has no sync device flow, so this runs on ``asyncio.run``."""
    settings = plugin.settings
    resolved = resolve_actor(settings, actor)
    if not resolved:
        raise LoginError("missing_actor", "Pass --actor, or set owner_actor in the profile settings.")
    if not settings.sign_in_resource:
        raise LoginError("missing_actor", "sign_in_resource is required to sign in.")
    if client is None:
        return asyncio.run(_login_with_new_client(plugin, resolved))
    return asyncio.run(_login(plugin, resolved, client))


async def _login_with_new_client(plugin: KeycardPlugin, actor: str) -> str:
    async with AsyncClient(plugin.settings.zone_url) as client:
        return await _login(plugin, actor, client)


async def _login(plugin: KeycardPlugin, actor: str, client: Any) -> str:
    settings = plugin.settings
    store = plugin.grants.store
    client_id = store.client_id()
    if client_id is None:
        registered = await client.register_client(
            ClientRegistrationRequest(
                client_name=f"Hermes Agent ({PLUGIN_ID})",
                grant_types=[GrantType.DEVICE_CODE, GrantType.REFRESH_TOKEN],
                token_endpoint_auth_method=TokenEndpointAuthMethod.NONE,
            )
        )
        client_id = str(registered.client_id)
        store.save_client_id(client_id)
    authorization = await client.request_device_authorization(
        client_id=client_id, resources=[settings.sign_in_resource]
    )
    url = authorization.verification_uri_complete or authorization.verification_uri
    print(f"Open {url} and enter the code {authorization.user_code}", file=sys.stderr)
    response = await client.wait_for_device_token(authorization, client_id=client_id)
    if not response.refresh_token:
        raise LoginError(
            "refresh_token_missing",
            "The zone issued no refresh token, so the sign-in could not outlive its "
            "first access token; nothing was stored.",
        )
    if settings.verify_sign_in:
        expected = settings.actors.get(actor)
        info = await client.userinfo(response.access_token)
        if not expected or info.sub != expected:
            raise LoginError(
                "sign_in_mismatch",
                "The signed-in user is not the one mapped for this actor; nothing was stored.",
            )
    expires_in = response.expires_in if response.expires_in is not None else 300
    store.save(
        actor,
        SignIn(
            client_id=client_id,
            refresh_token=response.refresh_token,
            access_token=response.access_token,
            expires_at=plugin.grants.clock() + float(expires_in),
        ),
    )
    return actor


def token(plugin: KeycardPlugin, resource: str, *, actor: str | None = None, as_self: bool = False) -> str:
    """A token for ``resource`` the way the middleware would acquire it for the
    actor (default ``owner_actor``), or as-itself."""
    pattern = "as_self" if as_self else None
    resolved = None if as_self else resolve_actor(plugin.settings, actor)
    access, needed = plugin.grants.acquire(resolved, [resource], pattern)
    if needed is not None or access.has_error():
        error = access.get_error() or {}
        raise RuntimeError(str(error.get("message") or (needed.reason if needed else "error")))
    if access.has_resource_error(resource):
        raise RuntimeError(str((access.get_resource_error(resource) or {}).get("message")))
    return access.access(resource).access_token

