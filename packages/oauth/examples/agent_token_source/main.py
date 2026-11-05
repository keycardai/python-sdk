"""Get a resource token from the local Keycard agent and call the resource with it.

Run under `keycard run` (which sets KEYCARD_SOCKET), or point KEYCARD_SOCKET at
the `keycard agent` daemon's socket and set KEYCARD_ORG and KEYCARD_ZONE.
"""

import asyncio
import os
import sys

import httpx

from keycardai.oauth import (
    AgentError,
    AgentTokenSource,
    ResourceAuth,
    is_application_not_found,
    is_authorization_required,
    is_declined,
    is_prompter_unavailable,
)


async def main() -> int:
    resource = os.environ.get("RESOURCE")
    if not resource:
        print("set RESOURCE to the resource indicator to request a token for")
        return 2

    source = AgentTokenSource(
        workload="agent-token-source-example",
        app_id=os.environ.get("KEYCARD_APP_ID", ""),
        org=os.environ.get("KEYCARD_ORG", ""),
        zone=os.environ.get("KEYCARD_ZONE", ""),
    )
    scopes = [s for s in os.environ.get("SCOPES", "").split(",") if s]

    try:
        token = await source.resource_token(resource, *scopes)
    except AgentError as err:
        if is_declined(err):
            print(f"declined: {err.message}")
        elif is_authorization_required(err):
            print(f"authorization required: {err.message}")
        elif is_prompter_unavailable(err):
            print("this host cannot show an approval prompt")
        elif is_application_not_found(err):
            print("KEYCARD_APP_ID names no application in this zone")
        else:
            print(err)
        return 1

    print(f"token for {resource}, expires in {token.expires_in}s")

    if call := os.environ.get("CALL_URL"):
        auth = ResourceAuth(source, resource, *scopes)
        async with httpx.AsyncClient(auth=auth) as client:
            response = await client.get(call)
        print(f"{call} -> {response.status_code}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
