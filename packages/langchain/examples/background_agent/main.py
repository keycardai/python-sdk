"""A background agent with no user anywhere: a morning PR-review digest.

The agent runs as itself (Access.as_self()): resource access is
attributed to the application alone, the GitHub credential lives in the zone
(vaulted or brokered), and every fetch is an audit event. Nothing in this
process or its environment holds a GitHub credential.

Run: uv run main.py
(In real use this is a cron entry; running it by hand is the same thing.)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import httpx
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage

from keycardai.langchain import (
    Access,
    KeycardGrantMiddleware,
    KeycardIdentity,
    get_access_context,
)

GITHUB = os.environ.get("KEYCARD_GITHUB_RESOURCE", "https://api.github.com")
REPOS = [
    r.strip()
    for r in os.environ.get("DIGEST_REPOS", "langchain-ai/langchain").split(",")
    if r.strip()
]

# One client for the process: connection reuse across tool calls, bounded waits.
_http = httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))


@tool
def list_open_pull_requests() -> str:
    """List open pull requests across the configured repositories."""
    access = get_access_context()
    if access.has_error():
        return f"Cannot reach GitHub: {access.get_error()['message']}"
    if access.has_resource_error(GITHUB):
        return f"GitHub access not granted: {access.get_resource_error(GITHUB)}"

    headers = {
        "Authorization": f"Bearer {access.access(GITHUB).access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    now = datetime.now(timezone.utc)
    report: dict[str, list[dict] | str] = {}
    for repo in REPOS:
        response = _http.get(
            f"{GITHUB}/repos/{repo}/pulls",
            params={"state": "open", "per_page": 20},
            headers=headers,
        )
        if response.status_code != 200:
            report[repo] = f"error {response.status_code}: {response.text[:200]}"
            continue
        report[repo] = [
            {
                "number": pr["number"],
                "title": pr["title"],
                "author": pr["user"]["login"],
                "draft": pr["draft"],
                "age_days": (now - datetime.fromisoformat(pr["created_at"])).days,
            }
            for pr in response.json()
        ]
    return json.dumps(report, indent=2)


def _text_of(message) -> str:
    """Final text from a message, whether content is a string or block list."""
    content = message.content
    if isinstance(content, str):
        return content
    return "\n".join(
        b.get("text", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    ).strip()


SYSTEM_PROMPT = (
    "You compile a morning review digest for a maintainer. Fetch the open "
    "pull requests, then write a short digest: what needs review first, "
    "what is a draft and can wait, and anything that looks stuck. "
    "Plain prose, under 200 words."
)


def main() -> None:
    keycard = KeycardGrantMiddleware(
        zone_url=os.environ["KEYCARD_ZONE_URL"],
        resources=[GITHUB],
        client_id=os.environ["KEYCARD_CLIENT_ID"],
        client_secret=os.environ["KEYCARD_CLIENT_SECRET"],
        # No authorization_url / sign_in_url on purpose: there is no user in
        # this process, so access failures are errors, never consent pauses.
    )
    agent = create_agent(
        model=ChatAnthropic(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-5"),
            max_tokens=4096,
        ),
        tools=[list_open_pull_requests],
        system_prompt=SYSTEM_PROMPT,
        middleware=[keycard],
        context_schema=KeycardIdentity,
    )

    result = agent.invoke(
        {"messages": [HumanMessage("Compile this morning's review digest.")]},
        context=Access.as_self(),
    )
    print(_text_of(result["messages"][-1]))


if __name__ == "__main__":
    main()
