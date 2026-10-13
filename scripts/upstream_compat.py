"""Test each framework integration against its upstream's latest release (SDK-22).

Mirror of typescript-sdk's scripts/upstream_compat.py (#242, #243) for this
repo. Nothing in CI runs when an upstream framework ships, and the riskiest
declarations here are open: ``fastmcp>=3.1.0`` counts fastmcp 4.1 as supported
without anyone having tested that pairing. This script takes the (package,
upstream) pairs from .github/upstream-compat.json and, for one entry at a time:

1. Resolves the upstream version to test: the latest stable release on PyPI
   (no prereleases, no yanked files), whether or not the declared specifier
   covers it, or the version passed in. A version that already passed on main
   is skipped on scheduled runs.
2. Syncs the package's environment the way the justfile does for one package,
   pins the upstream (and any companions) into that environment with
   ``uv pip install``, and runs the package's typecheck and tests. The pin
   lives in the virtualenv only; pyproject.toml and uv.lock change only when
   the outcome is a PR.
3. Acts on the outcome:
   * green and outside the declared specifier: a PR that widens the specifier
     and relocks, on branch ``upstream/<package>-<upstream>-<version>``, titled
     ``fix(keycardai-<package>): support <upstream> <version>`` so merging it
     releases. An existing branch or PR for that version is updated, never
     duplicated.
   * green and inside: nothing to open; the tested version is recorded.
   * red: one issue labeled ``upstream-compat`` per (upstream, version), with
     the tail of the failing output, whether the version is inside the
     specifier (inside means users installing today already get the break),
     and the run link. A later run that fails differently comments on it.

Specifier rules:
* A floor-only specifier (``>=3.1.0``) covers every later release, so it never
  needs a PR: green records, red files an issue that says users are affected.
* A capped specifier (``>=2.0.0,<3.0``) gets a PR when a green version lands
  above the cap. A version on the floor's line keeps the floor and moves the
  cap past it; a version on a new line (a new major) starts ``>=version,<cap``
  on that line, and the PR body says the move is breaking for users on the old
  major so a human picks the release scope.
* A 0.x upstream treats each minor as potentially breaking: a capped 0.x
  specifier moves its cap to the next minor (``>=0.20.0,<0.24.0``).

State lives on the ``upstream-compat-state`` branch as state.json, keyed by
``<package>:<upstream>`` and merged by key, with the push retried from a fresh
fetch when a parallel matrix job moved the branch first.

Inputs come from the environment so the workflow can pass event context:

    ENTRY        the config entry: ``<package>:<upstream>``, or a package name
                 when that package has one entry
    VERSION      the upstream version to test (default: latest stable)
    DRY_RUN      "true" prints the PR or issue instead of opening it
    RUN_URL      the Actions run, linked from issues and PR bodies
    STATE_BRANCH the state branch (default below)
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

CONFIG_PATH = ".github/upstream-compat.json"
DEFAULT_STATE_BRANCH = "upstream-compat-state"
STATE_FILE = "state.json"
STATE_WRITE_ATTEMPTS = 4
ISSUE_LABEL = "upstream-compat"
TAIL_LINES = 60
PYPI_JSON = "https://pypi.org/pypi/{name}/json"

# Packages outside the uv workspace keep their own lock and virtualenv.
STANDALONE_PACKAGES = {"fastmcp"}


@dataclass
class Entry:
    package: str
    upstream: str
    field: str
    companions: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.package}:{self.upstream}"

    @property
    def name(self) -> str:
        return f"keycardai-{self.package}"

    @property
    def extra(self) -> str | None:
        """The optional-dependencies group the upstream is declared in, if any."""
        prefix = "project.optional-dependencies."
        return self.field[len(prefix) :] if self.field.startswith(prefix) else None


@dataclass
class Outcome:
    green: bool
    tail: str


def load_config(path: str) -> list[Entry]:
    with open(path) as f:
        data = json.load(f)
    return [Entry(**entry) for entry in data["entries"]]


def pick_entry(entries: list[Entry], wanted: str) -> Entry:
    """The entry named by ``<package>:<upstream>``, or by package when that package has exactly one."""
    by_key = [e for e in entries if e.key == wanted]
    if by_key:
        return by_key[0]
    by_package = [e for e in entries if e.package == wanted]
    if len(by_package) == 1:
        return by_package[0]
    if by_package:
        raise SystemExit(
            f"ENTRY {wanted!r} is ambiguous; use one of: {', '.join(e.key for e in by_package)}"
        )
    raise SystemExit(f"ENTRY {wanted!r} is not in {CONFIG_PATH}")


# --- specifiers --------------------------------------------------------------


def is_stable(text: str) -> bool:
    try:
        v = Version(text)
    except InvalidVersion:
        return False
    return not (v.is_prerelease or v.is_devrelease)


def in_range(spec_text: str, version: str) -> bool:
    """Whether a PEP 440 specifier (empty means any) covers a version."""
    return SpecifierSet(spec_text).contains(Version(version), prereleases=False)


def range_floor(spec_text: str) -> str:
    for spec in SpecifierSet(spec_text):
        if spec.operator in (">=", "==", "~=", ">"):
            return spec.version
    raise ValueError(f"no lower bound in specifier {spec_text!r}")


def has_cap(spec_text: str) -> bool:
    return any(
        spec.operator in ("<", "<=", "==", "~=") for spec in SpecifierSet(spec_text)
    )


def _cap_after(v: Version) -> str:
    """The exclusive upper bound that treats the next breaking release as out: next major, or next minor for 0.x."""
    if v.major == 0:
        return f"0.{v.minor + 1}.0"
    return f"{v.major + 1}.0"


def is_new_major(spec_text: str, version: str) -> bool:
    """Whether the version leaves the floor's compatibility line (a new major, or a new minor for 0.x)."""
    f, v = Version(range_floor(spec_text)), Version(version)
    if f.major == 0 or v.major == 0:
        return (f.major, f.minor) != (v.major, v.minor)
    return f.major != v.major


def widened_range(spec_text: str, version: str) -> str:
    """The declared specifier after it grows to include version.

    Floor-only specifiers already cover it and come back unchanged. A capped
    specifier keeps its floor and moves the cap past the version when the
    version is on the floor's line; a version on a new line (new major, or
    new minor for 0.x) starts a fresh ``>=version,<cap`` on that line.
    Comparators other than the floor and the cap (``!=`` pins) are kept.
    """
    if in_range(spec_text, version):
        return spec_text
    v = Version(version)
    floor = Version(range_floor(spec_text))
    kept = [
        str(s)
        for s in SpecifierSet(spec_text)
        if s.operator not in (">=", ">", "<", "<=", "==", "~=")
    ]
    low = str(floor) if not is_new_major(spec_text, version) and floor <= v else version
    return ",".join([f">={low}", f"<{_cap_after(v)}", *sorted(kept)])


def decide(green: bool, inside: bool) -> str:
    """pr, record, or issue."""
    if not green:
        return "issue"
    return "record" if inside else "pr"


# --- dedupe -----------------------------------------------------------------


def branch_name(entry: Entry, version: str) -> str:
    return f"upstream/{entry.package}-{entry.upstream}-{version}"


def pr_title(entry: Entry, version: str) -> str:
    return f"fix({entry.name}): support {entry.upstream} {version}"


def issue_title(entry: Entry, version: str) -> str:
    return f"{entry.upstream} {version} breaks {entry.name}"


def find_by_title(items: list[dict], title: str) -> int | None:
    for item in items:
        if item.get("title") == title:
            return item["number"]
    return None


def already_tested(state: dict, entry: Entry, version: str) -> bool:
    return state.get(entry.key, {}).get("version") == version


def tail(text: str, lines: int = TAIL_LINES) -> str:
    return "\n".join(text.rstrip().splitlines()[-lines:])


def issue_body(
    entry: Entry, version: str, declared: str, inside: bool, out: str, run_url: str
) -> str:
    where = (
        f"inside the declared specifier `{declared}`, so users installing {entry.name} today already get this"
        if inside
        else f"outside the declared specifier `{declared}`, so users are not affected until the specifier widens"
    )
    return (
        f"{entry.upstream} {version} is {where}.\n\n"
        f"Typecheck and tests of `packages/{entry.package}` against it, tail of the output:\n\n"
        f"```\n{out}\n```\n\n"
        f"Run: {run_url or '(local run)'}\n"
    )


def pr_body(entry: Entry, version: str, old: str, new: str, run_url: str) -> str:
    breaking = (
        f"\n\n{version} starts a new compatibility line for {entry.upstream}, and the new specifier no longer "
        f"covers the line `{old}` named, so for users on that line this change is breaking: pick the release "
        f"scope (`fix` or `feat!`) before merging.\n"
        if is_new_major(old, version)
        else "\n"
    )
    return (
        "## Summary\n\n"
        f"{entry.upstream} {version} is the latest stable release and sits outside "
        f"{entry.name}'s declared specifier `{old}`. The package's typecheck and tests pass against it "
        f"with no code change, so the `{entry.field}` specifier moves to `{new}` and the lock "
        f"resolves {version}.{breaking}\n"
        f"Opened by the upstream compatibility check (SDK-22). Run: {run_url or '(local run)'}\n"
    )


def failed_differently(issue_body_text: str, out: str) -> bool:
    return out.strip() not in issue_body_text


# --- pyproject ----------------------------------------------------------------

DEP_RE_TEMPLATE = (
    r'(?P<q>["\'])(?P<name>{name})(?P<extras>\[[^\]]*\])?(?P<spec>[^"\']*)(?P=q)'
)


def read_declared(pyproject_text: str, upstream: str, extra: str | None) -> str:
    """The specifier the pyproject declares for upstream in project.dependencies or the named extra."""
    section = _section(pyproject_text, extra)
    m = re.search(
        DEP_RE_TEMPLATE.format(name=re.escape(upstream)), section, re.IGNORECASE
    )
    if not m:
        raise ValueError(
            f"{upstream} is not declared in {'project.dependencies' if extra is None else extra}"
        )
    return m.group("spec").split(";")[0].strip()


def replace_declared(
    pyproject_text: str, upstream: str, extra: str | None, new_spec: str
) -> str:
    """pyproject text with upstream's specifier replaced, leaving every other byte as it was."""
    section = _section(pyproject_text, extra)
    pattern = re.compile(
        DEP_RE_TEMPLATE.format(name=re.escape(upstream)), re.IGNORECASE
    )

    def swap(m: re.Match) -> str:
        marker = m.group("spec").split(";", 1)
        rest = f";{marker[1]}" if len(marker) == 2 else ""
        return f"{m.group('q')}{m.group('name')}{m.group('extras') or ''}{new_spec}{rest}{m.group('q')}"

    new_section, n = pattern.subn(swap, section, count=1)
    if n == 0:
        raise ValueError(f"{upstream} is not declared")
    return pyproject_text.replace(section, new_section, 1)


def _section(text: str, extra: str | None) -> str:
    """The text of the dependency list upstream lives in: ``dependencies = [...]`` or ``<extra> = [...]`` under optional-dependencies."""
    if extra is None:
        m = re.search(r"^dependencies\s*=\s*\[.*?^\]", text, re.MULTILINE | re.DOTALL)
    else:
        opt = re.search(
            r"^\[project\.optional-dependencies\](.*?)(?=^\[)",
            text,
            re.MULTILINE | re.DOTALL,
        )
        m = (
            re.search(
                rf"^{re.escape(extra)}\s*=\s*\[.*?^\]",
                opt.group(1),
                re.MULTILINE | re.DOTALL,
            )
            if opt
            else None
        )
    if not m:
        raise ValueError(
            f"no dependency list for {'project.dependencies' if extra is None else extra}"
        )
    return m.group(0)


# --- shell ------------------------------------------------------------------


def run(
    cmd: list[str], cwd: str, check: bool = True, **kwargs
) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd), f"(in {cwd})", flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, **kwargs)


def pypi_versions(upstream: str) -> dict[str, list[dict]]:
    with urllib.request.urlopen(PYPI_JSON.format(name=upstream), timeout=30) as resp:
        return json.load(resp)["releases"]


def latest_stable(releases: dict[str, list[dict]]) -> str:
    """The highest stable version with at least one file that is not yanked."""
    stable = [
        v
        for v, files in releases.items()
        if is_stable(v) and any(not f.get("yanked") for f in files)
    ]
    return str(max(stable, key=Version))


def pin_specs(entry: Entry, version: str) -> list[str]:
    """``upstream==version`` plus each companion unpinned, so the resolver picks what that upstream needs."""
    return [f"{entry.upstream}=={version}", *entry.companions]


def environment(repo_root: str, entry: Entry) -> tuple[str, str, list[str]]:
    """(sync cwd, venv path, extra ty flags) for the package: the workspace venv, or the standalone package's own."""
    pkg_dir = os.path.join(repo_root, "packages", entry.package)
    if entry.package in STANDALONE_PACKAGES:
        venv = os.path.join(pkg_dir, ".venv")
        return pkg_dir, venv, ["--project", pkg_dir, "--python", venv]
    return repo_root, os.path.join(repo_root, ".venv"), []


def install_and_test(repo_root: str, entry: Entry, version: str) -> Outcome:
    pkg_dir = os.path.join(repo_root, "packages", entry.package)
    sync_cwd, venv, ty_flags = environment(repo_root, entry)
    sync = (
        ["uv", "sync", "--extra", "test"]
        if sync_cwd == pkg_dir
        else ["uv", "sync", "--all-packages", "--all-extras"]
    )
    run(sync, sync_cwd)
    python = os.path.join(venv, "bin", "python")
    output = []
    steps = [
        (
            ["uv", "pip", "install", "--python", python, *pin_specs(entry, version)],
            repo_root,
        ),
        (
            [
                "uv",
                "run",
                "--frozen",
                "--no-sync",
                "ty",
                "check",
                *ty_flags,
                os.path.join(pkg_dir, "src"),
            ],
            repo_root,
        ),
        (
            [
                python,
                "-m",
                "pytest",
                "tests/",
                "-v",
                "-p",
                "no:cacheprovider",
                "--disable-warnings",
            ],
            pkg_dir,
        ),
    ]
    for cmd, cwd in steps:
        result = run(cmd, cwd, check=False, capture_output=True)
        output.append(result.stdout + result.stderr)
        last = next(
            (line for line in reversed(output[-1].splitlines()) if line.strip()), ""
        )
        print(f"  exit {result.returncode}: {last.strip()}", flush=True)
        if result.returncode != 0:
            return Outcome(False, tail("\n".join(output)))
    return Outcome(True, tail("\n".join(output)))


def apply_range(repo_root: str, entry: Entry, new_spec: str) -> list[str]:
    """Write the specifier into the package's pyproject and relock; returns the paths to commit."""
    pkg_dir = os.path.join(repo_root, "packages", entry.package)
    pyproject = os.path.join(pkg_dir, "pyproject.toml")
    with open(pyproject) as f:
        text = f.read()
    with open(pyproject, "w") as f:
        f.write(replace_declared(text, entry.upstream, entry.extra, new_spec))
    lock_cwd = pkg_dir if entry.package in STANDALONE_PACKAGES else repo_root
    run(["uv", "lock", "--upgrade-package", entry.upstream], lock_cwd)
    rel = os.path.relpath
    return [
        rel(pyproject, repo_root),
        rel(os.path.join(lock_cwd, "uv.lock"), repo_root),
    ]


def gh_json(args: list[str], cwd: str) -> list[dict]:
    out = run(["gh", *args], cwd, capture_output=True).stdout
    return json.loads(out) if out.strip() else []


def open_or_update_pr(
    repo_root: str,
    entry: Entry,
    version: str,
    title: str,
    body: str,
    paths: list[str],
    dry_run: bool,
) -> None:
    branch = branch_name(entry, version)
    if dry_run:
        print(f"dry run: would push {branch} and open or update PR {title!r}\n{body}")
        return
    run(["git", "checkout", "-B", branch], repo_root)
    run(["git", "add", *paths], repo_root)
    run(
        [
            "git",
            "commit",
            "-m",
            title,
            "-m",
            "Opened by the upstream compatibility check (SDK-22).",
        ],
        repo_root,
    )
    run(["git", "push", "--force-with-lease", "-u", "origin", branch], repo_root)
    existing = find_by_title(
        gh_json(
            [
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "open",
                "--json",
                "number,title",
            ],
            repo_root,
        ),
        title,
    )
    if existing is not None:
        run(["gh", "pr", "edit", str(existing), "--body", body], repo_root)
        print(f"updated PR #{existing}")
        return
    run(
        ["gh", "pr", "create", "--head", branch, "--title", title, "--body", body],
        repo_root,
    )
    print("created PR")


def open_or_update_issue(
    repo_root: str, title: str, body: str, out: str, dry_run: bool
) -> None:
    if dry_run:
        print(
            f"dry run: would open or update issue {title!r} (label {ISSUE_LABEL})\n{body}"
        )
        return
    run(
        [
            "gh",
            "label",
            "create",
            ISSUE_LABEL,
            "--force",
            "--description",
            "An upstream framework release breaks a package",
        ],
        repo_root,
    )
    issues = gh_json(
        [
            "issue",
            "list",
            "--label",
            ISSUE_LABEL,
            "--state",
            "open",
            "--search",
            f'"{title}" in:title',
            "--json",
            "number,title,body",
        ],
        repo_root,
    )
    existing = find_by_title(issues, title)
    if existing is None:
        run(
            [
                "gh",
                "issue",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--label",
                ISSUE_LABEL,
            ],
            repo_root,
        )
        print("created issue")
        return
    issue = next(i for i in issues if i["number"] == existing)
    if failed_differently(issue.get("body", ""), out):
        run(
            [
                "gh",
                "issue",
                "comment",
                str(existing),
                "--body",
                "A later run failed differently:\n\n" + body,
            ],
            repo_root,
        )
        print(f"commented on issue #{existing}")
    else:
        print(f"issue #{existing} already records this failure")


# --- state ------------------------------------------------------------------


def read_state(repo_root: str, state_branch: str) -> dict:
    fetched = run(["git", "fetch", "origin", state_branch], repo_root, check=False)
    if fetched.returncode != 0:
        return {}
    shown = run(
        ["git", "show", f"origin/{state_branch}:{STATE_FILE}"],
        repo_root,
        check=False,
        capture_output=True,
    )
    return (
        json.loads(shown.stdout)
        if shown.returncode == 0 and shown.stdout.strip()
        else {}
    )


def merge_state(current: dict, key: str, record: dict) -> dict:
    """The branch state with this entry's record set; every other key is left as the branch has it."""
    merged = dict(current)
    merged[key] = record
    return merged


def write_state_once(
    repo_root: str, state_branch: str, state: dict, message: str
) -> bool:
    """Commit state on top of origin/<state_branch> (an orphan when the branch is absent) and push; False when the push is rejected."""
    with tempfile.TemporaryDirectory(prefix="upstream-compat-state-") as wt:
        exists = run(
            ["git", "rev-parse", "--verify", f"origin/{state_branch}"],
            repo_root,
            check=False,
            capture_output=True,
        )
        if exists.returncode == 0:
            run(["git", "worktree", "add", wt, f"origin/{state_branch}"], repo_root)
            run(["git", "checkout", "-B", state_branch], wt)
        else:
            run(["git", "worktree", "add", "--detach", wt], repo_root)
            run(["git", "checkout", "--orphan", state_branch], wt)
            run(["git", "rm", "-rfq", "."], wt, check=False)
        try:
            with open(os.path.join(wt, STATE_FILE), "w") as f:
                json.dump(state, f, indent=2, sort_keys=True)
                f.write("\n")
            run(["git", "add", STATE_FILE], wt)
            run(["git", "commit", "-m", message], wt)
            pushed = run(["git", "push", "origin", state_branch], wt, check=False)
        finally:
            run(["git", "worktree", "remove", "--force", wt], repo_root)
    return pushed.returncode == 0


def record_state(
    repo_root: str,
    state_branch: str,
    key: str,
    record: dict,
    message: str,
    attempts: int = STATE_WRITE_ATTEMPTS,
    read: Callable[[str, str], dict] = read_state,
    write_once: Callable[[str, str, dict, str], bool] = write_state_once,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Set key on the branch's current state and push, re-reading the tip and retrying when another job pushed first.

    The matrix jobs run in parallel and all write the same branch, so a push
    can be rejected because a sibling moved the tip (or created the branch
    this job found absent). Each attempt starts from a fresh fetch, so the
    merge builds on whatever landed, never on a second orphan.
    """
    for attempt in range(1, attempts + 1):
        state = merge_state(read(repo_root, state_branch), key, record)
        if write_once(repo_root, state_branch, state, message):
            return state
        print(
            f"state push rejected (attempt {attempt} of {attempts}); another job moved {state_branch}",
            flush=True,
        )
        if attempt < attempts:
            sleep(random.uniform(1.0, 4.0))
    sys.exit(
        f"state write failed after {attempts} attempts: could not record {key} on {state_branch}. "
        "The compatibility test itself was green; this is a state-branch write failure."
    )


# --- main -------------------------------------------------------------------


def main() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    entry = pick_entry(
        load_config(os.path.join(repo_root, CONFIG_PATH)), os.environ["ENTRY"]
    )
    dry_run = os.environ.get("DRY_RUN") == "true"
    run_url = os.environ.get("RUN_URL", "")
    state_branch = os.environ.get("STATE_BRANCH") or DEFAULT_STATE_BRANCH
    requested = os.environ.get("VERSION", "").strip()

    version = (
        requested
        if requested and requested != "latest"
        else latest_stable(pypi_versions(entry.upstream))
    )
    with open(
        os.path.join(repo_root, "packages", entry.package, "pyproject.toml")
    ) as f:
        declared = read_declared(f.read(), entry.upstream, entry.extra)
    inside = in_range(declared, version)
    print(
        f"{entry.name}: {entry.upstream} {version} is {'inside' if inside else 'outside'} the declared {entry.field} specifier {declared or '(any)'}"
    )

    state = read_state(repo_root, state_branch)
    if not requested and already_tested(state, entry, version):
        print(f"{entry.upstream} {version} already passed on main; nothing to do")
        return

    outcome = install_and_test(repo_root, entry, version)
    action = decide(outcome.green, inside)
    print(f"outcome: {'green' if outcome.green else 'red'}; action: {action}")

    if action == "issue":
        body = issue_body(entry, version, declared, inside, outcome.tail, run_url)
        open_or_update_issue(
            repo_root, issue_title(entry, version), body, outcome.tail, dry_run
        )
        sys.exit(1)

    if action == "pr":
        new_spec = widened_range(declared, version)
        paths = [] if dry_run else apply_range(repo_root, entry, new_spec)
        open_or_update_pr(
            repo_root,
            entry,
            version,
            pr_title(entry, version),
            pr_body(entry, version, declared, new_spec, run_url),
            paths,
            dry_run,
        )
        return

    if dry_run:
        print(f"dry run: would record {entry.key} = {version} on {state_branch}")
        return
    record_state(
        repo_root,
        state_branch,
        entry.key,
        {"version": version, "run": run_url},
        f"{entry.name}: {entry.upstream} {version} passes",
    )


if __name__ == "__main__":
    main()
