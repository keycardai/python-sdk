"""A user-facing agent acting on behalf of the caller: a calendar assistant.

The caller's Keycard token is exchanged per tool call for a Google Calendar
token (RFC 8693), so every calendar read is attributed to agent-for-user in
the zone's audit log. When the user has not granted calendar access yet, the
run pauses with a LangGraph interrupt; this CLI prints the consent link,
waits, then resumes the same run.

Run: uv run main.py "what's on my calendar today?"
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import httpx
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from keycardai.langchain import (
    KeycardGrantMiddleware,
    KeycardIdentity,
    get_access_context,
)

CALENDAR = os.environ.get(
    "KEYCARD_CALENDAR_RESOURCE", "https://www.googleapis.com/calendar/v3"
)

_http = httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))


@tool
def list_events(days_ahead: int = 0) -> str:
    """List the user's calendar events for one day, days_ahead days from today."""
    access = get_access_context()
    if access.has_error():
        return f"Calendar unavailable: {access.get_error()['message']}"
    if access.has_resource_error(CALENDAR):
        return f"Calendar access not granted: {access.get_resource_error(CALENDAR)}"

    day = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    response = _http.get(
        f"{CALENDAR}/calendars/primary/events",
        params={
            "timeMin": start.isoformat(),
            "timeMax": (start + timedelta(days=1)).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
        },
        headers={"Authorization": f"Bearer {access.access(CALENDAR).access_token}"},
    )
    if response.status_code != 200:
        return f"Calendar API error {response.status_code}: {response.text[:200]}"
    events = response.json().get("items", [])
    if not events:
        return "No events that day."
    return "\n".join(
        f"- {e.get('start', {}).get('dateTime', e.get('start', {}).get('date'))}: "
        f"{e.get('summary', '(no title)')}"
        for e in events
    )


def main() -> None:
    question = " ".join(sys.argv[1:]) or "What's on my calendar today?"
    identity = KeycardIdentity(subject_token=os.environ["KEYCARD_SUBJECT_TOKEN"])

    keycard = KeycardGrantMiddleware(
        zone_url=os.environ["KEYCARD_ZONE_URL"],
        resources=[CALENDAR],
        client_id=os.environ["KEYCARD_CLIENT_ID"],
        client_secret=os.environ["KEYCARD_CLIENT_SECRET"],
        # A missing grant pauses the run instead of failing; the loop below
        # prints the link and resumes after consent.
        authorization_url=os.environ.get(
            "KEYCARD_AUTHORIZATION_URL", os.environ["KEYCARD_ZONE_URL"]
        ),
    )
    agent = create_agent(
        model=ChatAnthropic(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-5"),
            max_tokens=4096,
        ),
        tools=[list_events],
        middleware=[keycard],
        context_schema=KeycardIdentity,
        checkpointer=InMemorySaver(),  # interrupts require a checkpointer
    )
    config = {"configurable": {"thread_id": "cli"}}

    result = agent.invoke(
        {"messages": [HumanMessage(question)]}, config, context=identity
    )
    while result.get("__interrupt__"):
        payload = result["__interrupt__"][0].value
        print(f"\n{payload['message']}")
        print(f"  {payload.get('authorization_url') or payload.get('sign_in_url')}")
        input("\nPress Enter after granting access to resume... ")
        # Runtime context is not checkpointed: a resume re-supplies identity.
        result = agent.invoke(Command(resume="granted"), config, context=identity)

    final = result["messages"][-1]
    content = final.content
    if isinstance(content, list):
        content = "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
    print(content)


if __name__ == "__main__":
    main()
