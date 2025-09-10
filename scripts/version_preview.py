#!/usr/bin/env python3
"""
Version preview tool for workspace packages.

This script analyzes packages with unreleased changes and provides
version bump previews using commitizen.

Usage:
  python scripts/version_preview.py [--format json|markdown|github-summary]

Formats:
  json           - JSON output suitable for programmatic use
  markdown       - Markdown output for documentation
  github-summary - GitHub Actions summary format (default)
"""

import argparse
import json
import subprocess
import sys


def run_command(cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """Run a command and return exit code, stdout, stderr."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def get_changed_packages() -> list[dict[str, str]]:
    """Get packages with unreleased changes using the existing changelog script."""
    exit_code, stdout, stderr = run_command([
        "uv", "run", "python", "scripts/changelog.py", "changes", "--output-format", "json"
    ])

    if exit_code != 0:
        raise Exception(f"Failed to detect changed packages: {stderr}")

    if not stdout.strip():
        return []

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        raise Exception(f"Failed to parse package changes JSON: {e}") from e


def get_version_info(package_dir: str, package_name: str) -> dict[str, str]:
    """Get version information for a package using commitizen."""
    exit_code, stdout, stderr = run_command([
        "uv", "run", "cz", "bump", "--dry-run"
    ], cwd=package_dir)

    result = {
        "package_name": package_name,
        "package_dir": package_dir,
        "has_changes": False,
        "current_version": None,
        "next_version": None,
        "increment": None,
        "error": None
    }

    if exit_code != 0:
        result["error"] = f"Failed to get version info: {stderr}"
        return result

    if not stdout.strip():
        result["error"] = "No output from commitizen"
        return result

    # Parse commitizen output
    lines = stdout.split('\n')
    for line in lines:
        if "→" in line and line.strip().startswith("bump:"):
            # Example: "bump: keycardai-oauth 0.1.0 → 0.2.0"
            parts = line.split()
            if len(parts) >= 5 and "→" in parts:
                arrow_index = parts.index("→")
                if arrow_index >= 1:
                    result["current_version"] = parts[arrow_index - 1]
                    result["next_version"] = parts[arrow_index + 1]
                    result["has_changes"] = True
        elif line.strip().startswith("increment detected:"):
            # Example: "increment detected: MINOR"
            parts = line.split(":")
            if len(parts) >= 2:
                result["increment"] = parts[1].strip()

    return result


def format_as_json(version_info: list[dict[str, str]]) -> str:
    """Format version information as JSON."""
    return json.dumps(version_info, indent=2)


def format_as_markdown(version_info: list[dict[str, str]]) -> str:
    """Format version information as Markdown."""
    if not version_info:
        return "No packages with unreleased changes detected."

    output = ["# Release Preview", ""]

    # Version changes section
    output.extend(["## Expected Version Changes", ""])
    for info in version_info:
        if info["has_changes"]:
            output.append(f"- **{info['package_name']}**: {info['current_version']} → {info['next_version']} ({info['increment']})")
        elif info["error"]:
            output.append(f"- **{info['package_name']}**: Error - {info['error']}")
        else:
            output.append(f"- **{info['package_name']}**: No version change detected")

    output.append("")

    # Package details section
    output.extend(["## Package Details", ""])
    for info in version_info:
        output.append(f"- **Package**: {info['package_name']}")
        output.append(f"  - **Directory**: {info['package_dir']}")
        if info["has_changes"]:
            output.append(f"  - **Current Version**: {info['current_version']}")
            output.append(f"  - **Next Version**: {info['next_version']}")
            output.append(f"  - **Increment Type**: {info['increment']}")
        output.append("")

    return "\n".join(output)


def format_as_github_summary(version_info: list[dict[str, str]]) -> str:
    """Format version information for GitHub Actions summary."""
    if not version_info:
        return "ℹ️ No packages with unreleased changes detected."

    output = ["## 📦 Release Preview", ""]
    output.append("This analysis shows the expected release impact:", "")

    # Version changes section
    output.extend(["### 📈 Expected Version Changes", "", "```"])
    for info in version_info:
        if info["has_changes"]:
            output.append(f"{info['package_name']}: {info['current_version']} → {info['next_version']} ({info['increment']})")
        elif info["error"]:
            output.append(f"{info['package_name']}: Error - {info['error']}")
        else:
            output.append(f"{info['package_name']}: No version change detected")
    output.extend(["```", ""])

    # Package details section
    output.extend(["### 📋 Package Details", "", "```json"])
    package_details = []
    for info in version_info:
        package_details.append({
            "package_name": info["package_name"],
            "package_dir": info["package_dir"],
            "has_changes": info["has_changes"],
            "current_version": info["current_version"],
            "next_version": info["next_version"],
            "increment": info["increment"]
        })
    output.append(json.dumps(package_details, indent=2))
    output.extend(["```", ""])

    return "\n".join(output)


def main():
    """Main function with argument parsing."""
    parser = argparse.ArgumentParser(
        description="Generate version preview for packages with unreleased changes"
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown", "github-summary"],
        default="github-summary",
        help="Output format (default: github-summary)"
    )

    args = parser.parse_args()

    try:
        # Get packages with changes
        changed_packages = get_changed_packages()

        if not changed_packages:
            if args.format == "json":
                print("[]")
            elif args.format == "markdown":
                print("No packages with unreleased changes detected.")
            else:  # github-summary
                print("ℹ️ No packages with unreleased changes detected.")
            return

        # Get version information for each package
        version_info = []
        for package in changed_packages:
            info = get_version_info(package["package_dir"], package["package_name"])
            version_info.append(info)

        # Format and output results
        if args.format == "json":
            print(format_as_json(version_info))
        elif args.format == "markdown":
            print(format_as_markdown(version_info))
        else:  # github-summary
            print(format_as_github_summary(version_info))

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
