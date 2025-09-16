#!/usr/bin/env python3
"""
Bump package version script.

This script handles version bumping for a specific package using commitizen,
including retry logic for pushing changes to avoid race conditions.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path


def run_command(cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """Run a command and return exit code, stdout, and stderr."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def configure_git() -> None:
    """Configure git for automated commits."""
    print("Configuring git...")
    run_command(["git", "config", "--local", "user.email", "action@github.com"])
    run_command(["git", "config", "--local", "user.name", "GitHub Action"])


def pull_latest_changes() -> bool:
    """Pull latest changes from origin/main, handling local modifications."""
    print("Checking for local changes before pulling...")

    # Check if there are local changes
    exit_code, stdout, stderr = run_command(["git", "status", "--porcelain"])

    if exit_code != 0:
        print(f"Failed to check git status: {stderr}")
        return False

    has_local_changes = bool(stdout.strip())

    if has_local_changes:
        print(f"Found local changes:\n{stdout}")
        print("Stashing local changes before pulling...")

        # Stash local changes
        exit_code, stdout, stderr = run_command(["git", "stash", "push", "-m", "Auto-stash before version bump"])

        if exit_code != 0:
            print(f"Failed to stash local changes: {stderr}")
            return False

        print("Successfully stashed local changes")

    print("Pulling latest changes from origin/main...")
    exit_code, stdout, stderr = run_command(["git", "pull", "origin", "main"])

    if exit_code != 0:
        print(f"Failed to pull latest changes: {stderr}")

        # If we stashed changes, try to restore them
        if has_local_changes:
            print("Attempting to restore stashed changes...")
            run_command(["git", "stash", "pop"])

        return False

    print("Successfully pulled latest changes")

    # If we stashed changes, restore them
    if has_local_changes:
        print("Restoring stashed changes...")
        exit_code, stdout, stderr = run_command(["git", "stash", "pop"])

        if exit_code != 0:
            print(f"Warning: Failed to restore stashed changes: {stderr}")

            # If the conflict is in uv.lock, we can try to resolve it automatically
            if "uv.lock" in stderr:
                print("Detected uv.lock conflict. Attempting automatic resolution...")

                # Re-sync dependencies to regenerate uv.lock
                sync_exit_code, sync_stdout, sync_stderr = run_command(["uv", "sync", "--all-extras", "--all-packages"])

                if sync_exit_code == 0:
                    print("Successfully regenerated uv.lock after conflict")
                    # Drop the stash since we've resolved it
                    run_command(["git", "stash", "drop"])
                else:
                    print(f"Failed to regenerate uv.lock: {sync_stderr}")
                    print("Manual intervention may be required")
            else:
                print("Non-uv.lock conflict detected. Manual intervention may be required")

            # Don't fail the process - the pull succeeded

    return True


def run_bump(package_dir: str, package_name: str) -> bool:
    """Run commitizen bump in the specified package directory."""
    print(f"Running version bump for {package_name} in {package_dir}...")

    exit_code, stdout, stderr = run_command(
        ["uv", "run", "cz", "bump", "--changelog", "--yes"],
        cwd=package_dir
    )

    if exit_code != 0:
        print(f"Failed to bump version: {stderr}")
        return False

    print("Version bump completed successfully")
    print(stdout)
    return True


def push_changes_with_retry(max_attempts: int = 3) -> bool:
    """Push changes to origin/main with retry logic."""
    # First, let's check what tags were created
    exit_code, stdout, stderr = run_command(["git", "tag", "--list", "--sort=-version:refname"])
    if exit_code == 0 and stdout:
        print(f"Local tags found: {stdout}")
        # Show the most recent tag
        recent_tags = stdout.split('\n')[:3]
        print(f"Most recent tags: {recent_tags}")

    for attempt in range(1, max_attempts + 1):
        print(f"Attempting to push changes (attempt {attempt}/{max_attempts})...")

        exit_code, stdout, stderr = run_command(
            ["git", "push", "origin", "main", "--follow-tags"]
        )

        if exit_code == 0:
            print(f"Successfully pushed changes on attempt {attempt}")

            # Explicitly push tags to ensure they're uploaded
            print("Explicitly pushing tags...")
            tag_exit_code, tag_stdout, tag_stderr = run_command(
                ["git", "push", "origin", "--tags"]
            )

            if tag_exit_code == 0:
                print("Successfully pushed tags")
            else:
                print(f"Warning: Failed to push tags: {tag_stderr}")
                # Don't fail the whole process for tag push failure

            return True

        print(f"Push failed on attempt {attempt}: {stderr}")

        if attempt < max_attempts:
            print("Pulling latest changes and retrying...")

            # Use the improved pull function that handles local changes
            if not pull_latest_changes():
                print("Failed to pull latest changes during retry")
                continue

            print("Waiting 2 seconds before retry...")
            time.sleep(2)
        else:
            print(f"Failed to push after {max_attempts} attempts")
            return False

    return False


def bump_package(package_name: str, package_dir: str) -> bool:
    """
    Bump version for a specific package.

    Args:
        package_name: Name of the package (e.g., keycardai-oauth)
        package_dir: Directory path of the package (e.g., packages/oauth)

    Returns:
        True if successful, False otherwise
    """
    print(f"Starting version bump for {package_name} package...")

    # Ensure package directory exists
    if not Path(package_dir).exists():
        print(f"Error: Package directory {package_dir} does not exist")
        return False

    # Configure git for automated commits
    configure_git()

    # Pull latest changes to avoid conflicts
    if not pull_latest_changes():
        return False

    # Run the version bump
    if not run_bump(package_dir, package_name):
        return False

    # Push changes with retry logic
    if not push_changes_with_retry():
        return False

    print(f"Successfully completed version bump for {package_name}")
    return True


def main():
    """Main function with argument parsing."""
    parser = argparse.ArgumentParser(
        description="Bump package version using commitizen"
    )
    parser.add_argument(
        "package_name",
        help="Name of the package to bump (e.g., keycardai-oauth)"
    )
    parser.add_argument(
        "package_dir",
        help="Directory of the package (e.g., packages/oauth)"
    )
    parser.add_argument(
        "--max-retry-attempts",
        type=int,
        default=3,
        help="Maximum number of push retry attempts (default: 3)"
    )

    args = parser.parse_args()

    # Set max retry attempts globally
    global MAX_RETRY_ATTEMPTS
    MAX_RETRY_ATTEMPTS = args.max_retry_attempts

    # Run the bump
    success = bump_package(args.package_name, args.package_dir)

    if not success:
        print("Version bump failed")
        sys.exit(1)

    print("Version bump completed successfully")


if __name__ == "__main__":
    main()
