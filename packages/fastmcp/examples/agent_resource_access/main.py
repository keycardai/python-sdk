"""MCP tools that reach a resource with a token from the local Keycard agent.

Unlike delegated_access, which exchanges the MCP caller's token, this server
has no inbound user token to exchange. It runs beside the Keycard CLI (under
`keycard run`, or reaching the `keycard agent` daemon) and asks the CLI for a
token, which a human approves the first time.

The resource and the URL the token is sent to are fixed by configuration, not
by tool arguments: a token is a credential, and a tool that sent it wherever
the model asked would hand it to any prompt that named a URL.
"""

import base64
import json
import os

import httpx
from fastmcp import FastMCP

from keycardai.oauth import AgentError, AgentTokenSource, OAuthError, ResourceAuth

RESOURCE = os.getenv("RESOURCE", "")
RESOURCE_URL = os.getenv("RESOURCE_URL", "")
SCOPES = [s for s in os.getenv("SCOPES", "").split(",") if s]

source = AgentTokenSource(
    workload=os.getenv("KEYCARD_WORKLOAD", "agent-resource-access"),
    app_id=os.getenv("KEYCARD_APP_ID", ""),
    org=os.getenv("KEYCARD_ORG", ""),
    zone=os.getenv("KEYCARD_ZONE", ""),
)

mcp = FastMCP("Agent Resource Access")


def _failure(err: OAuthError) -> dict:
    if isinstance(err, AgentError):
        return {"error": err.code, "message": err.message}
    return {"error": type(err).__name__, "message": str(err)}


def _claims(token: str) -> dict | None:
    """The token's payload, for display only; nothing here verifies it."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(payload)
    except ValueError:
        return None
    return {k: claims.get(k) for k in ("iss", "sub", "aud", "scope", "exp")}


@mcp.tool()
async def describe_resource_token() -> dict:
    """Obtain a token for the configured resource and describe it.

    The token itself is never returned: it is a credential, and the tool's
    output reaches the model.
    """
    if not RESOURCE:
        return {"error": "RESOURCE is not set"}
    try:
        token = await source.resource_token(RESOURCE, *SCOPES)
    except OAuthError as err:
        return _failure(err)
    claims = _claims(token.access_token)
    return {
        "resource": RESOURCE,
        "expires_in": token.expires_in,
        "shape": "compact JWS" if claims is not None else "opaque",
        "claims": claims,
    }


@mcp.tool()
async def call_resource(path: str = "/") -> dict:
    """GET a path on the configured resource URL with a token attached."""
    if not RESOURCE or not RESOURCE_URL:
        return {"error": "RESOURCE and RESOURCE_URL must both be set"}
    url = RESOURCE_URL.rstrip("/") + "/" + path.lstrip("/")
    auth = ResourceAuth(source, RESOURCE, *SCOPES)
    try:
        async with httpx.AsyncClient(auth=auth) as client:
            response = await client.get(url)
    except OAuthError as err:
        return _failure(err)
    return {"url": url, "status": response.status_code, "body": response.text[:2000]}


def main():
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()
