#!/usr/bin/env python3
"""
Keep one release preview comment per PR.

The body comes from scripts/release_preview.py, capped so it fits on a
screen. The first run creates the comment; every later run edits it in
place, including the case where the PR no longer releases anything. A PR
that never released anything gets no comment.

Usage:
  python scripts/pr_comment.py [--pr-number PR_NUMBER] [--dry-run]

Environment Variables:
  GITHUB_TOKEN - GitHub token for API access (used by gh CLI)
  GH_REPO or GITHUB_REPOSITORY - owner/repo
  PR_NUMBER - Pull request number
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_preview import (  # noqa: E402
    SIGNATURE,
    cap_text,
    collect_preview,
    job_summary_url,
    render_preview,
)

# Well under GitHub's 65,536-character limit on a comment body.
MAX_COMMENT_CHARS = 12_000


def run_command(cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """Run a command and return exit code, stdout, stderr."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def repository() -> str:
    repo = os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise Exception("Repository not specified. Set GH_REPO or GITHUB_REPOSITORY.")
    return repo


def find_existing_comment(comments: list[dict]) -> int | None:
    """Return the numeric id of this workflow's comment, if the PR has one."""
    for comment in comments:
        if SIGNATURE in comment.get("body", ""):
            return comment["id"]
    return None


def get_existing_comment_id(repo: str, pr_number: int) -> int | None:
    """List the PR's comments through the REST API, whose ids the PATCH endpoint accepts."""
    exit_code, stdout, stderr = run_command(
        ["gh", "api", f"repos/{repo}/issues/{pr_number}/comments", "--paginate", "--slurp"]
    )
    if exit_code != 0:
        print(f"Warning: Failed to get PR comments: {stderr}")
        return None
    try:
        pages = json.loads(stdout)
    except json.JSONDecodeError as e:
        print(f"Warning: Failed to parse PR comments JSON: {e}")
        return None
    comments = [comment for page in pages for comment in page]
    return find_existing_comment(comments)


def build_comment_body(max_chars: int = MAX_COMMENT_CHARS) -> tuple[str, bool]:
    """Return the comment body and whether the PR releases anything."""
    preview = collect_preview()
    text = render_preview(preview)
    footer = f"\n\n---\n*{SIGNATURE}.*"
    text = cap_text(text, max_chars - len(footer), job_summary_url())
    return text + footer, bool(preview.releases or preview.errors)


def decide_action(releases_anything: bool, existing_id: int | None) -> str:
    if existing_id is not None:
        return "update"
    if releases_anything:
        return "create"
    return "skip"


def post_comment(repo: str, pr_number: int, action: str, existing_id: int | None, body: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(body)
        temp_file = f.name
    try:
        if action == "update":
            cmd = [
                "gh", "api", f"repos/{repo}/issues/comments/{existing_id}",
                "--method", "PATCH", "--field", f"body=@{temp_file}",
            ]
            exit_code, _, stderr = run_command(cmd)
            if exit_code == 0:
                print(f"Updated existing comment {existing_id}")
                return
            print(f"Failed to update comment {existing_id} ({stderr}); creating a new one")
        cmd = ["gh", "pr", "comment", str(pr_number), "--repo", repo, "--body-file", temp_file]
        exit_code, _, stderr = run_command(cmd)
        if exit_code != 0:
            raise Exception(f"Failed to create comment: {stderr}")
        print("Created new comment")
    finally:
        Path(temp_file).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or update the PR's release preview comment")
    parser.add_argument("--pr-number", type=int, help="Pull request number (or PR_NUMBER env var)")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the action and body without touching the PR"
    )
    args = parser.parse_args()

    pr_number = args.pr_number or os.getenv("PR_NUMBER")
    if not pr_number:
        print("Error: PR number not provided via --pr-number or PR_NUMBER env var", file=sys.stderr)
        sys.exit(1)
    pr_number = int(pr_number)

    try:
        repo = repository()
        body, releases_anything = build_comment_body()
        existing_id = get_existing_comment_id(repo, pr_number)
        action = decide_action(releases_anything, existing_id)

        if args.dry_run:
            target = f" comment {existing_id}" if existing_id is not None else ""
            print(f"dry run: would {action}{target} on PR #{pr_number} ({len(body)} chars)")
            print(body)
            return
        if action == "skip":
            print("Nothing to release and no existing comment; skipping")
            return
        post_comment(repo, pr_number, action, existing_id, body)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
