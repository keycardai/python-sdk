"""Test a package at its declared keycardai-* sibling floors (ECO-379).

The uv workspace resolves siblings to the local checkout, so the normal test
job runs each package against sibling code its published floor does not
promise. This script installs the package's built wheel in a clean venv with
every keycardai-* dependency pinned to its declared floor from PyPI and runs
the package's test suite there.

A floor may name a sibling version that is not on PyPI yet. That is the
sequenced-merge case: a carrier and its consumer merge together and the
carrier has not released. Two things turn that failure into a skip:

* the ``floors-bootstrap`` label on a feature PR (``FLOORS_BOOTSTRAP=true``);
* a bump PR opened by the release app whose branch names exactly the
  (package, version) pair that is missing (ECO-381). The bump PR is the one
  that publishes that version, so it can never see it on PyPI first.

Inputs come from the environment so the workflow can pass event context:

    PACKAGE           packages/<PACKAGE> is the package under test
    FLOORS_BOOTSTRAP  "true" when the PR carries the floors-bootstrap label
    PR_HEAD_REF       the PR's head branch name
    PR_AUTHOR         the PR author's login
    RELEASE_APP_LOGIN the release app's bot login (default below)
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable

import tomllib

DEFAULT_RELEASE_APP_LOGIN = "keycard-sdk-release[bot]"

# Mirrors bump_branch_name in bump_package.py: bump/<release-line>/<package>-<version>.
# The release line never contains a slash and the version starts with a digit,
# so the last "-<digit>" split is the package/version boundary.
BUMP_BRANCH_RE = re.compile(
    r"^bump/[^/]+/(?P<package>keycardai-[a-z0-9-]+)-(?P<version>[0-9][0-9A-Za-z.]*)$"
)

FLOOR_RE = re.compile(
    r"(keycardai-[a-z0-9-]+)(\[[^\]]*\])?\s*>=\s*([0-9][0-9A-Za-z.]*)\s*(,.*)?"
)


def parse_bump_pr(
    head_ref: str, author: str, release_app_login: str
) -> tuple[str, str] | None:
    """Return (package, version) when the PR is a bump PR opened by the release app.

    Both signals are required: a matching branch name from someone else is not
    a bump PR, and a release-app PR on another branch is not one either.
    """
    if author != release_app_login:
        return None
    m = BUMP_BRANCH_RE.fullmatch(head_ref)
    if not m:
        return None
    return m.group("package"), m.group("version")


def read_floors(pyproject_path: str) -> tuple[str, dict[str, str]]:
    """Return the package name and its keycardai-* floors, or exit on an unreadable floor."""
    with open(pyproject_path, "rb") as f:
        project = tomllib.load(f)["project"]
    name = project["name"]

    floors: dict[str, str] = {}
    for req in project["dependencies"]:
        if not req.startswith("keycardai-"):
            continue
        m = FLOOR_RE.fullmatch(req)
        if not m:
            print(
                f"::error::{name}: cannot read a floor from sibling requirement {req!r}; "
                "expected keycardai-x>=A.B.C"
            )
            sys.exit(1)
        floors[m.group(1)] = m.group(3)
    return name, floors


def on_pypi(sibling: str, version: str) -> bool:
    try:
        urllib.request.urlopen(
            f"https://pypi.org/pypi/{sibling}/{version}/json"
        ).close()
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def check_ahead_of_pypi(
    name: str,
    floors: dict[str, str],
    *,
    bootstrap: bool,
    bump_pr: tuple[str, str] | None,
    exists: Callable[[str, str], bool] = on_pypi,
) -> bool:
    """Return True when installation should proceed, False when the check is skipped.

    Exits 1 when a floor is ahead of PyPI and neither skip applies.
    """
    ahead = [(s, v) for s, v in floors.items() if not exists(s, v)]
    if not ahead:
        return True

    if bump_pr is not None and all((s, v) == bump_pr for s, v in ahead):
        for sibling, version in ahead:
            print(
                f"{name}: floor {sibling}>={version} is the version this bump PR releases; "
                "it cannot be on PyPI before the bump merges"
            )
        print("skipping the floors check for this package")
        return False

    level = "warning" if bootstrap else "error"
    for sibling, version in ahead:
        print(
            f"::{level}::{name}: floor {sibling}>={version} is ahead of PyPI; "
            f"{sibling} {version} is not published yet"
        )
    if bootstrap:
        print(
            "floors-bootstrap label is set on this PR; skipping the floors check for this package"
        )
        return False
    print(
        "Release the sibling first, or add the floors-bootstrap label to the PR for a sequenced merge."
    )
    sys.exit(1)


def install_and_test(name: str, pkg_dir: str, pins: list[str]) -> None:
    (wheel,) = glob.glob("dist/*.whl")
    venv = ".venv-floors"
    python = f"{venv}/bin/python"
    subprocess.run(["uv", "venv", venv, "--python", "3.12"], check=True)

    # --no-sources keeps the siblings coming from the index at the pinned
    # floor instead of the workspace checkout: the floor is what users install.
    install = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            python,
            "--no-sources",
            f"{wheel}[test]",
            *pins,
        ],
        capture_output=True,
        text=True,
    )
    sys.stdout.write(install.stdout)
    sys.stderr.write(install.stderr)
    if install.returncode != 0:
        # uv reports resolver failures as a "×" headline followed by wrapped "╰─▶" detail.
        lines = [line.strip() for line in install.stderr.splitlines() if line.strip()]
        start = next((i for i, line in enumerate(lines) if line.startswith("×")), None)
        if start is not None:
            first = " ".join(line.lstrip("×╰─▶ ") for line in lines[start:])
        else:
            first = next(
                (
                    line
                    for line in lines
                    if not line.startswith(
                        ("INFO ", "Using ", "Creating ", "Activate ")
                    )
                ),
                "install failed",
            )
        print(f"::error::{name} does not install with {' '.join(pins)}: {first}")
        sys.exit(1)
    subprocess.run(["uv", "pip", "list", "--python", python], check=True)

    tests = subprocess.run(
        [
            os.path.abspath(python),
            "-m",
            "pytest",
            "tests/",
            "-v",
            "-p",
            "no:cacheprovider",
        ],
        cwd=pkg_dir,
        capture_output=True,
        text=True,
    )
    sys.stdout.write(tests.stdout)
    sys.stderr.write(tests.stderr)
    if tests.returncode != 0:
        lines = tests.stdout.splitlines()
        first = next(
            (line for line in lines if line.startswith(("E   ", "FAILED ", "ERROR "))),
            f"pytest exited {tests.returncode}",
        )
        print(
            f"::error::{name} fails its tests with {' '.join(pins)} (declared floors of {name}): {first.strip()}"
        )
        sys.exit(1)

    print(f"{name} passes its tests at sibling floors {' '.join(pins)}")


def main() -> None:
    package = os.environ["PACKAGE"]
    bootstrap = os.environ.get("FLOORS_BOOTSTRAP") == "true"
    bump_pr = parse_bump_pr(
        os.environ.get("PR_HEAD_REF", ""),
        os.environ.get("PR_AUTHOR", ""),
        os.environ.get("RELEASE_APP_LOGIN") or DEFAULT_RELEASE_APP_LOGIN,
    )
    pkg_dir = f"packages/{package}"

    name, floors = read_floors(f"{pkg_dir}/pyproject.toml")
    if not floors:
        print(f"{name} declares no keycardai-* dependencies; nothing to check")
        return

    pins = [f"{sibling}=={floor}" for sibling, floor in floors.items()]
    print(f"{name}: sibling floors {' '.join(pins)}")
    if bump_pr is not None:
        print(f"bump PR detected: {bump_pr[0]} {bump_pr[1]}")

    if not check_ahead_of_pypi(name, floors, bootstrap=bootstrap, bump_pr=bump_pr):
        return

    install_and_test(name, pkg_dir, pins)


if __name__ == "__main__":
    main()
