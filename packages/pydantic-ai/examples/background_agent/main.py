"""A background agent with no user anywhere: a morning PR-review digest.

The agent runs as itself (Access.as_self()): resource access is attributed to
the application alone, the GitHub credential lives in the zone (vaulted or
brokered), and every fetch is an audit event. Nothing in this process or its
environment holds a GitHub credential. As-itself never pauses: a denied grant
is a per-resource error the tool reports.

Run: uv run main.py
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import httpx
from pydantic_ai import Agent

from keycardai.pydantic_ai import (
    Access,
    KeycardGrant,
    KeycardIdentity,
    get_access_context,
)

GITHUB = os.environ.get("KEYCARD_GITHUB_RESOURCE", "https://api.github.com")
REPOS = [
    r.strip()
    for r in os.environ.get("DIGEST_REPOS", "pydantic/pydantic-ai").split(",")
    if r.strip()
]

keycard = KeycardGrant(
    zone_url=os.environ["KEYCARD_ZONE_URL"],
    resources=[GITHUB],
    client_id=os.environ["KEYCARD_CLIENT_ID"],
    client_secret=os.environ["KEYCARD_CLIENT_SECRET"],
)

agent = Agent(
    "anthropic:claude-sonnet-4-5",
    deps_type=KeycardIdentity,
    capabilities=[keycard],
    instructions="Summarize the open pull requests as a short review digest.",
)

_http = httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))


@agent.tool_plain
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
    now = datetime.now(UTC)
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


def main() -> None:
    result = agent.run_sync("Write today's PR review digest.", deps=Access.as_self())
    print(result.output)


if __name__ == "__main__":
    main()
