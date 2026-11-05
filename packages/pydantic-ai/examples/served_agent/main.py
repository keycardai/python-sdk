"""A Pydantic AI agent served over HTTP, acting for whoever called.

keycardai-starlette verifies the zone bearer on every request and exposes the
verified token as ``request.user.access_token``. The chat route hands that
token to the run as its identity, so each run acts on behalf of its caller and
nothing else carries identity. Pydantic AI's AG-UI adapter speaks the chat
protocol, including the pause for consent and the resume after it.
"""

from __future__ import annotations

import os

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models import Model
from pydantic_ai.ui.ag_ui import AGUIAdapter

from keycardai.oauth import AsyncClient
from keycardai.oauth.server import ClientSecret
from keycardai.pydantic_ai import (
    Access,
    KeycardGrant,
    KeycardIdentity,
    get_access_context,
)
from keycardai.starlette import AuthProvider, KeycardUser, requires

CALENDAR = os.environ.get(
    "KEYCARD_CALENDAR_RESOURCE", "https://www.googleapis.com/calendar/v3"
)

_http = httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))


def build_agent(
    model: Model | str,
    *,
    zone_url: str,
    client_id: str | None = None,
    client_secret: str | None = None,
    authorization_url: str | None = None,
    client: AsyncClient | None = None,
) -> Agent[KeycardIdentity, str | DeferredToolRequests]:
    """The agent: KeycardGrant plus one tool that reads the exchanged token."""
    keycard = KeycardGrant(
        zone_url=zone_url,
        resources=[CALENDAR],
        client_id=client_id,
        client_secret=client_secret,
        # A missing grant pauses the run with the consent link instead of
        # failing. The AG-UI adapter carries the pause to the client.
        authorization_url=authorization_url or zone_url,
        client=client,
    )
    agent: Agent[KeycardIdentity, str | DeferredToolRequests] = Agent(
        model,
        deps_type=KeycardIdentity,
        # The pause for consent ends the run with DeferredToolRequests, which
        # the AG-UI adapter turns into an interrupt; the agent must allow it.
        output_type=[str, DeferredToolRequests],
        capabilities=[keycard],
        instructions="You are a calendar assistant. Use the tool, then answer briefly.",
    )

    @agent.tool_plain
    def list_events() -> str:
        """List the caller's calendar events for today."""
        access = get_access_context()
        if access.has_error():
            return f"Calendar unavailable: {access.get_error()['message']}"
        if access.has_resource_error(CALENDAR):
            return f"Calendar access not granted: {access.get_resource_error(CALENDAR)}"
        token = access.access(CALENDAR).access_token
        response = _http.get(
            f"{CALENDAR}/calendars/primary/events",
            params={"singleEvents": "true", "orderBy": "startTime", "maxResults": 10},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code != 200:
            return f"Calendar error {response.status_code}: {response.text[:200]}"
        items = response.json().get("items", [])
        if not items:
            return "No events."
        return "\n".join(f"- {e.get('summary', '(no title)')}" for e in items)

    return agent


def build_app(
    auth: AuthProvider, agent: Agent[KeycardIdentity, str | DeferredToolRequests]
) -> FastAPI:
    """The FastAPI app: zone bearer verification and one gated chat route."""
    app = FastAPI()
    auth.install(app)

    @app.post("/chat")
    @requires("authenticated")
    async def chat(request: Request) -> Response:
        user: KeycardUser = request.user
        return await AGUIAdapter.dispatch_request(
            request, agent=agent, deps=Access.on_behalf_of(user.access_token)
        )

    return app


def main() -> None:
    zone_url = os.environ["KEYCARD_ZONE_URL"]
    credential = ClientSecret(
        (os.environ["KEYCARD_CLIENT_ID"], os.environ["KEYCARD_CLIENT_SECRET"])
    )
    auth = AuthProvider(
        zone_url=zone_url,
        application_credential=credential,
        # The agent's own resource: the verifier rejects tokens minted for
        # anything else.
        audience=os.environ["KEYCARD_AGENT_RESOURCE"],
    )
    agent = build_agent(
        "anthropic:claude-sonnet-4-5",
        zone_url=zone_url,
        client_id=os.environ["KEYCARD_CLIENT_ID"],
        client_secret=os.environ["KEYCARD_CLIENT_SECRET"],
        authorization_url=os.environ.get("KEYCARD_AUTHORIZATION_URL"),
    )
    uvicorn.run(
        build_app(auth, agent),
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
