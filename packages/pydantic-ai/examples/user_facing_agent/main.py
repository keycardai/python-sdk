"""A user-facing agent acting on behalf of the caller: a calendar assistant.

The caller's Keycard token is exchanged per tool call for a Google Calendar
token, so access is attributed to agent-for-user. When the user has not granted
calendar access yet, the run pauses with a deferred tool approval; this CLI
prints the consent link, waits, then resumes the same run.

Run: uv run main.py "what's on my calendar today?"
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

import httpx
from pydantic_ai import Agent, DeferredToolRequests

from keycardai.pydantic_ai import (
    Access,
    KeycardGrant,
    KeycardIdentity,
    get_access_context,
)

CALENDAR = os.environ.get(
    "KEYCARD_CALENDAR_RESOURCE", "https://www.googleapis.com/calendar/v3"
)

keycard = KeycardGrant(
    zone_url=os.environ["KEYCARD_ZONE_URL"],
    resources=[CALENDAR],
    client_id=os.environ["KEYCARD_CLIENT_ID"],
    client_secret=os.environ["KEYCARD_CLIENT_SECRET"],
    # A missing grant pauses the run instead of failing; the loop below prints
    # the link and resumes after consent. Defaults to the zone, as in the
    # LangChain example.
    authorization_url=os.environ.get(
        "KEYCARD_AUTHORIZATION_URL", os.environ["KEYCARD_ZONE_URL"]
    ),
    # Set this to pause for sign-in too (no identity, or an expired token).
    sign_in_url=os.environ.get("KEYCARD_SIGN_IN_URL"),
)

agent = Agent(
    "anthropic:claude-sonnet-4-5",
    deps_type=KeycardIdentity,
    output_type=[str, DeferredToolRequests],
    capabilities=[keycard],
    instructions="You are a calendar assistant. Use the tool, then answer briefly.",
)

_http = httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))


@agent.tool_plain
def list_events(days_ahead: int = 0) -> str:
    """List the user's calendar events for a day, 0 being today."""
    access = get_access_context()
    if access.has_error():
        return f"Calendar unavailable: {access.get_error()['message']}"
    if access.has_resource_error(CALENDAR):
        return f"Calendar access not granted: {access.get_resource_error(CALENDAR)}"
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start += timedelta(days=days_ahead)
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
        return f"Calendar error {response.status_code}: {response.text[:200]}"
    items = response.json().get("items", [])
    if not items:
        return "No events."
    return "\n".join(
        f"- {e.get('start', {}).get('dateTime', e.get('start', {}).get('date'))}: {e.get('summary', '(no title)')}"
        for e in items
    )


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "What is on my calendar today?"
    identity = Access.on_behalf_of(os.environ["KEYCARD_SUBJECT_TOKEN"])

    result = agent.run_sync(question, deps=identity)
    while isinstance(result.output, DeferredToolRequests):
        # One human flow per pause: sign-in or consent, with the URL to open.
        for call in result.output.approvals:
            payload = result.output.metadata[call.tool_call_id]
            print(f"\n[{payload['type']}] {payload['message']}")
            print(payload.get("sign_in_url") or payload.get("authorization_url"))
        input("\nPress Enter after completing it to resume... ")
        results = result.output.build_results(approve_all=True)
        # Deps are not part of the message history: a resume re-supplies identity.
        result = agent.run_sync(
            message_history=result.all_messages(),
            deps=identity,
            deferred_tool_results=results,
        )
    print(result.output)


if __name__ == "__main__":
    main()
