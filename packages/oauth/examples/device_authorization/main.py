"""Device authorization grant (RFC 8628) example.

A process with no browser asks the zone for a user code, prints it, and polls
until a person approves the request on another device.

Configure in Keycard before running:

- A zone (KEYCARD_ZONE_URL), for example https://your-zone.keycard.cloud.
- An OAuth client (KEYCARD_CLIENT_ID) with the device code grant enabled. For
  a confidential client, also set KEYCARD_CLIENT_SECRET. Without it the
  example runs as a public client and sends only client_id.
- Optional: KEYCARD_SCOPES, space-separated (default "openid"). With openid,
  the zone adds its userinfo resource, so policy must allow it for this
  client.
- Optional: KEYCARD_RESOURCES, space-separated resource URLs. Each must be a
  Resource in the zone, or the request fails with invalid_target. With more
  than one, the example refreshes the token for the first resource.

Run:

    KEYCARD_ZONE_URL=... KEYCARD_CLIENT_ID=... uv run python main.py
"""

import asyncio
import os
import sys

from keycardai.oauth import (
    AsyncClient,
    DeviceAuthorization,
    DeviceAuthorizationError,
    DevicePollError,
    TokenResponse,
)
from keycardai.oauth.pkce import refresh_authorization
from keycardai.oauth.server.credentials import ClientSecret

MAX_RESUMES = 3


async def wait_for_token(
    client: AsyncClient, auth: DeviceAuthorization, client_auth: dict
) -> TokenResponse:
    """Poll until the person approves or denies.

    After a network error, a 429, or a 5xx, resume at the interval from the
    error, at most MAX_RESUMES times.
    """
    interval = None
    for attempt in range(MAX_RESUMES + 1):
        try:
            return await client.wait_for_device_token(
                auth, interval=interval, **client_auth
            )
        except DevicePollError as e:
            if attempt == MAX_RESUMES:
                raise
            print(f"Poll failed ({e.cause}); resuming in {e.interval}s.")
            interval = e.interval
    raise AssertionError("unreachable")


async def main() -> None:
    zone_url = os.environ.get("KEYCARD_ZONE_URL")
    client_id = os.environ.get("KEYCARD_CLIENT_ID")
    if not zone_url or not client_id:
        sys.exit("KEYCARD_ZONE_URL and KEYCARD_CLIENT_ID are required")
    scopes = os.environ.get("KEYCARD_SCOPES", "openid").split()
    resources = os.environ.get("KEYCARD_RESOURCES", "").split()

    # Every request in the flow, and the refresh, authenticates the same way.
    secret = os.environ.get("KEYCARD_CLIENT_SECRET")
    if secret:
        client_auth: dict = {"credential": ClientSecret((client_id, secret))}
    else:
        client_auth = {"client_id": client_id}

    async with AsyncClient(zone_url) as client:
        auth = await client.request_device_authorization(
            scopes=scopes, resources=resources, **client_auth
        )
        print("On another device, open:\n")
        print(f"  {auth.verification_uri_complete or auth.verification_uri}\n")
        print("and confirm the code:\n")
        print(f"  {auth.user_code}\n")
        expires_at = auth.expires_at.astimezone()
        print(f"Waiting for approval (expires at {expires_at:%H:%M:%S %Z})...")

        try:
            token = await wait_for_token(client, auth, client_auth)
        except DeviceAuthorizationError as e:
            if e.error == "access_denied":
                sys.exit("The request was denied.")
            if e.error == "expired_token":
                sys.exit("The code expired before anyone approved it. Run again.")
            raise

    print(
        f"Approved. Token type {token.token_type}, expires in {token.expires_in}s, "
        f"scopes {token.scope}."
    )
    print(f"refresh_token returned: {token.refresh_token is not None}")
    print(f"id_token returned: {token.id_token is not None}")

    # With more than one resource, the token is not for any one of them.
    # Refresh with one resource to get a token for it.
    if len(resources) < 2 or token.refresh_token is None:
        return
    narrowed = await refresh_authorization(
        issuer=zone_url,
        refresh_token=token.refresh_token,
        resources=resources[:1],
        **client_auth,
    )
    print(f"Token for {resources[0]}: expires in {narrowed.expires_in}s.")


if __name__ == "__main__":
    asyncio.run(main())
