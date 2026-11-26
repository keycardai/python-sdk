"""Get a resource token from the local Keycard agent and call the resource with it.

Keycard setup this example needs:

1. The Keycard CLI, signed in to a zone (`keycard auth signin`), where the
   resource named by RESOURCE is registered and the developer may obtain
   tokens for it. A third-party provider's resource may first need a one-time
   interactive authorization; the agent's error message names the command.
2. A running agent, either:
   - `keycard run -- uv run python main.py`, which starts a per-session agent
     for this process and sets KEYCARD_SOCKET, or
   - `keycard agent start`, the long-running multi-zone daemon. With
     KEYCARD_SOCKET unset the example finds the daemon's default socket, and
     KEYCARD_ORG and KEYCARD_ZONE must name the org and zone ids to mint in.
3. A host that can show the approval prompt. The first request for a
   resource and scope set waits until a human approves or declines it.
4. Optionally, a Keycard application in the zone whose identifier (as set on
   the application, not its public id) is given as KEYCARD_APP_ID. The prompt
   shows it marked self-asserted.

Environment:
    RESOURCE        the resource indicator to request a token for (required)
    SCOPES          comma-separated scopes to request
    KEYCARD_ORG     org id, for the multi-zone daemon
    KEYCARD_ZONE    zone id, for the multi-zone daemon
    KEYCARD_APP_ID  the application this example says it acts for
    CALL_URL        a URL on the resource to GET with the token attached
"""

import asyncio
import os
import sys

import httpx

from keycardai.oauth import (
    AgentError,
    AgentTokenSource,
    ConfigError,
    NetworkError,
    ResourceTransport,
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
    scopes = [s for s in os.environ.get("SCOPES", "").split(",") if s]

    source = AgentTokenSource(
        workload="agent-token-source-example",
        app_id=os.environ.get("KEYCARD_APP_ID", ""),
        org=os.environ.get("KEYCARD_ORG", ""),
        zone=os.environ.get("KEYCARD_ZONE", ""),
    )

    try:
        token = await source.resource_token(resource, scopes)
    except (ConfigError, NetworkError) as err:
        print(err)
        return 1
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

    expiry = "no expiry" if token.expires_in is None else f"expires in {token.expires_in}s"
    print(f"token for {resource}, {expiry}, granted scopes {token.scope or 'unknown'}")

    if call := os.environ.get("CALL_URL"):
        # The agent caches the token, so this second request is answered
        # without another prompt. The client's timeout bounds the GET, not
        # the wait for the token.
        auth = ResourceTransport(source, resource, scopes)
        async with httpx.AsyncClient(auth=auth) as client:
            response = await client.get(call)
        print(f"GET {call} -> {response.status_code}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
