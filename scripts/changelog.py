#!/usr/bin/env python3
"""
Changelog management tool for workspace packages.

Usage:
  python scripts/changelog.py validate [base_branch]
  python scripts/changelog.py preview [base_branch]
  python scripts/changelog.py changes [--output-format json|github]
  python scripts/changelog.py package <tag> [--output-format json|github]

Commands:
  validate    Validate commit messages and show changelog preview
  preview     Preview changelog changes for each package
  changes     Detect packages with unreleased changes
  package     Extract package information from GitHub tag
"""

import argparse
import glob
import json
import subprocess
import sys
from pathlib import Path

import tomllib


def run_command(cmd: list[str], cwd: str = None) -> tuple[int, str, str]:
    """Run a command and return exit code, stdout, stderr."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def get_merge_base(base_branch: str) -> str:
    """Get the merge base commit."""
    exit_code, stdout, stderr = run_command(["git", "merge-base", base_branch, "HEAD"])
    if exit_code != 0:
        raise Exception(f"Failed to get merge base: {stderr}")
    return stdout


def discover_workspace_packages() -> list[dict]:
    """Discover packages from the workspace configuration."""
    root_dir = Path(__file__).parent.parent
    pyproject_path = root_dir / "pyproject.toml"

    if not pyproject_path.exists():
        raise Exception(f"Could not find pyproject.toml at {pyproject_path}")

    try:
        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)
    except Exception as e:
        raise Exception(f"Failed to parse pyproject.toml: {e}") from e

    workspace_config = config.get("tool", {}).get("uv", {}).get("workspace", {})
    members = workspace_config.get("members", [])
    exclude = workspace_config.get("exclude", [])

    if not members:
        raise Exception("No workspace members found in pyproject.toml")

    packages = []

    for member_pattern in members:
        member_paths = glob.glob(str(root_dir / member_pattern))

        for member_path in member_paths:
            member_path = Path(member_path)

            relative_path = member_path.relative_to(root_dir)
            if any(relative_path.match(exclude_pattern) for exclude_pattern in exclude):
                continue

            package_pyproject = member_path / "pyproject.toml"
            if not package_pyproject.exists():
                continue

            try:
                with open(package_pyproject, "rb") as f:
                    package_config = tomllib.load(f)

                if "tool" in package_config and "commitizen" in package_config["tool"]:
                    package_name = (
                        package_config.get("project", {}).get("name") or
                        package_config.get("name") or
                        member_path.name
                    )

                    relative_package_path = str(relative_path)

                    package_info = {
                        "package_name": package_name,
                        "package_dir": relative_package_path,
                    }

                    packages.append(package_info)

            except Exception as e:
                raise Exception(f"Could not parse {package_pyproject}: {e}") from e

    if not packages:
        raise Exception("No packages with commitizen configuration found")
    return packages


def validate_commits_with_cz(base_branch: str) -> bool:
    """Validate commits using commitizen."""
    base_sha = get_merge_base(base_branch)
    rev_range = f"{base_sha}..HEAD"

    exit_code, stdout, _ = run_command(["git", "rev-list", rev_range])
    if exit_code != 0 or not stdout.strip():
        return True

    exit_code, stdout, stderr = run_command(
        ["uv", "run", "cz", "check", "--rev-range", rev_range]
    )

    if exit_code == 0:
        return True
    else:
        return False


def preview_changelog_changes(base_branch: str) -> None:
    """Preview changelog changes using commitizen for each package."""
    base_sha = get_merge_base(base_branch)
    rev_range = f"{base_sha}..HEAD"

    exit_code, stdout, _ = run_command(["git", "rev-list", rev_range])
    if exit_code != 0 or not stdout.strip():
        raise Exception("No commits to preview.")

    packages = discover_workspace_packages()

    for package in packages:
        package_name = package["package_name"]
        package_dir = package["package_dir"]
        exit_code, stdout, stderr = run_command(
            ["uv", "run", "cz", "changelog", "--dry-run"],
            cwd=package_dir
        )

        if exit_code == 0:
            print(f"Changelog for {package_name}:")
            if stdout.strip():
                print(stdout)
            else:
                print("No changelog changes for this package.")
        else:
            raise Exception(f"Could not generate changelog preview for {package_name}: {stderr}")


def parse_changelog_for_changes(package_dir: str) -> bool:
    """
    Check if a package has unreleased changes by running cz changelog --dry-run.

    Commitizen automatically detects unreleased changes since the last tag.
    Returns True if there are changes in the "Unreleased" section.
    """
    exit_code, stdout, stderr = run_command(
        ["uv", "run", "cz", "changelog", "--dry-run"],
        cwd=package_dir
    )

    if exit_code != 0:
        raise Exception(f"Could not generate changelog for {package_dir}: {stderr}")

    if not stdout.strip():
        raise Exception(f"No changelog output for {package_dir}")

    lines = stdout.split('\n')
    if not lines[0].strip().startswith("## Unreleased"):
        raise Exception(f"Expected '## Unreleased' at top of changelog output, got: {lines[0]}")

    changes = []
    for line in lines[1:]:
        line = line.strip()
        # Indicates previous release section
        if line.startswith("##"):
            break
        if line:
            changes.append(line)

    return len(changes) > 0


def detect_changed_packages() -> list[dict]:
    """Detect which packages have unreleased changes using commitizen."""
    all_packages = discover_workspace_packages()

    changed_packages = []

    for package in all_packages:
        package_dir = package["package_dir"]

        has_changes = parse_changelog_for_changes(package_dir)
        if has_changes:
            changed_packages.append(package)

    return changed_packages


def cmd_validate(args):
    """Handle validate command."""
    base_branch = args.base_branch

    if validate_commits_with_cz(base_branch):
        print("\n✅ All commit messages are valid!")
        sys.exit(0)
    else:
        print("\n❌ Some commit messages are invalid!")
        print("Please fix the commit messages to follow the conventional commit format.")
        print("You can use 'git rebase -i' to edit commit messages.")
        sys.exit(1)


def cmd_preview(args):
    """Handle preview command."""
    base_branch = args.base_branch
    preview_changelog_changes(base_branch)


def cmd_changes(args):
    """Handle changes command."""
    changed_packages = detect_changed_packages()

    if args.output_format == "json":
        print(json.dumps(changed_packages, indent=2))
    else:
        print(json.dumps(changed_packages))


def extract_package_from_tag(tag: str) -> dict:
    """
    Extract package information from a GitHub tag.

    Tags follow the pattern: <version>-<package-name>
    Examples:
    - 1.0.0-keycardai-oauth -> package: keycardai-oauth
    - 0.1.0-keycardai-mcp -> package: keycardai-mcp
    - 0.2.0-keycardai-mcp-fastmcp -> package: keycardai-mcp-fastmcp
    """
    if not tag:
        raise Exception("Tag cannot be empty")

    if tag.startswith("refs/tags/"):
        tag = tag[len("refs/tags/"):]

    all_packages = discover_workspace_packages()

    version = None
    package_suffix = None
    matched_package = None

    for package in all_packages:
        package_name = package["package_name"]

        if tag.endswith(f"-{package_name}"):
            version = tag[:-len(f"-{package_name}")]
            package_suffix = package_name
            matched_package = package
            break

    if not version or not package_suffix or not matched_package:
        raise Exception(f"No package found for tag '{tag}'. Expected format: <version>-<package-name>")

    return {
        "tag": tag,
        "version": version,
        "package_suffix": package_suffix,
        "package_name": matched_package["package_name"],
        "package_dir": matched_package["package_dir"]
    }


def cmd_package(args):
    """Handle package command."""
    try:
        package_info = extract_package_from_tag(args.tag)

        if args.output_format == "json":
            print(json.dumps(package_info, indent=2))
        else:
            print(json.dumps(package_info))

    except Exception as e:
        raise Exception(f"Failed to extract package info from tag '{args.tag}': {e}") from e


def main():
    """Main function with subcommands."""
    parser = argparse.ArgumentParser(
        description="Unified changelog management tool for workspace packages"
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    validate_parser = subparsers.add_parser('validate', help='Validate commit messages and show changelog preview')
    validate_parser.add_argument(
        'base_branch',
        nargs='?',
        default='origin/main',
        help='Base branch to compare against (default: origin/main)'
    )
    validate_parser.set_defaults(func=cmd_validate)

    preview_parser = subparsers.add_parser('preview', help='Preview changelog changes for each package')
    preview_parser.add_argument(
        'base_branch',
        nargs='?',
        default='origin/main',
        help='Base branch to compare against (default: origin/main)'
    )
    preview_parser.set_defaults(func=cmd_preview)

    changes_parser = subparsers.add_parser('changes', help='Detect packages with unreleased changes')
    changes_parser.add_argument(
        '--output-format',
        choices=['json', 'github'],
        default='github',
        help='Output format: json or github (default: github)'
    )
    changes_parser.set_defaults(func=cmd_changes)

    package_parser = subparsers.add_parser('package', help='Extract package information from GitHub tag')
    package_parser.add_argument(
        'tag',
        help='GitHub tag to extract package information from (e.g., 1.0.0-keycardai-oauth)'
    )
    package_parser.add_argument(
        '--output-format',
        choices=['json', 'github'],
        default='github',
        help='Output format: json or github (default: github)'
    )
    package_parser.set_defaults(func=cmd_package)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
