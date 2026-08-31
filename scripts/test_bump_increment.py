"""Increment-detection tests for the per-package commitizen configuration.

Each test builds a throwaway git repository whose ``pyproject.toml`` carries a
package's real ``[tool.commitizen]`` block (copied out of the checkout so the
assertions track the shipped config) and runs ``cz bump --dry-run`` against a
synthetic history. Run with:

    just test-release-tooling
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PACKAGES = {
    "keycardai-mcp": "packages/mcp",
    "keycardai-oauth": "packages/oauth",
    "keycardai-starlette": "packages/starlette",
    "keycardai-a2a": "packages/a2a",
    "keycardai-langchain": "packages/langchain",
    "keycardai-fastmcp": "packages/fastmcp",
    "keycardai": ".",
}

CZ_BLOCK = re.compile(
    r"^\[tool\.commitizen\].*?(?=^\[(?!tool\.commitizen)|\Z)",
    re.M | re.S,
)


def commitizen_config(package_dir: str) -> str:
    """Return the ``[tool.commitizen]`` section text of a package."""
    text = (REPO_ROOT / package_dir / "pyproject.toml").read_text()
    match = CZ_BLOCK.search(text)
    if match is None:
        raise AssertionError(f"no [tool.commitizen] block in {package_dir}")
    return match.group(0)


def git(*args: str, cwd: Path) -> None:
    # Signing is off: the synthetic history is throwaway and the ambient git
    # config may require a key the test runner does not hold.
    subprocess.run(
        ["git", "-c", "commit.gpgSign=false", "-c", "tag.gpgSign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def commit(message: str, *, cwd: Path) -> None:
    (cwd / "src.txt").write_text(message)
    git("add", "src.txt", cwd=cwd)
    git("commit", "-m", message, cwd=cwd)


class SyntheticRepo:
    """A git repo holding one package's commitizen config at ``version``."""

    def __init__(self, package_name: str, version: str) -> None:
        self.package_name = package_name
        self.version = version
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)

    def __enter__(self) -> SyntheticRepo:
        config = commitizen_config(PACKAGES[self.package_name])
        config = re.sub(
            r'^version = ".*"$', f'version = "{self.version}"', config, count=1, flags=re.M
        )
        (self.path / "pyproject.toml").write_text(config)
        git("init", "--initial-branch=main", cwd=self.path)
        git("config", "user.email", "test@example.com", cwd=self.path)
        git("config", "user.name", "Test", cwd=self.path)
        git("add", "pyproject.toml", cwd=self.path)
        git("commit", "-m", "chore: init", cwd=self.path)
        git("tag", f"{self.version}-{self.package_name}", cwd=self.path)
        return self

    def __exit__(self, *exc: object) -> None:
        self._tmp.cleanup()

    def commit(self, message: str) -> None:
        commit(message, cwd=self.path)

    def dry_run_version(self) -> str | None:
        """Return the version cz would bump to, or ``None`` when it finds none."""
        result = subprocess.run(
            [sys.executable, "-m", "commitizen", "bump", "--dry-run", "--yes"],
            cwd=self.path,
            capture_output=True,
            text=True,
            check=False,
        )
        output = f"{result.stdout}\n{result.stderr}"
        if result.returncode != 0:
            if "NO_COMMITS_FOUND" in output or "NO_COMMITS_TO_BUMP" in output:
                return None
            raise AssertionError(f"cz bump --dry-run failed: {output}")
        match = re.search(r"(\d+\.\d+\.\d+)\s*(?:→|->)\s*(\d+\.\d+\.\d+)", output)
        if match is None:
            raise AssertionError(f"could not parse cz output: {output}")
        return match.group(2)


class SiblingScopeIsolationTests(unittest.TestCase):
    """A sibling package's breaking change must not move this package."""

    def test_sibling_breaking_change_does_not_major_keycardai_mcp(self) -> None:
        # Reproduces the 3.0.0 misfire: keycardai-mcp sat at 2.1.0 with only a
        # fix of its own, while keycardai-oauth's `feat!` commit shared the
        # window. Commitizen's default classifier read that as MAJOR.
        with SyntheticRepo("keycardai-mcp", "2.1.0") as repo:
            repo.commit("feat(keycardai-oauth)!: multi-resource web-app flow")
            repo.commit("fix(keycardai-mcp): restore MCP 2.x HTTP connections")
            self.assertEqual(repo.dry_run_version(), "2.1.1")

    def test_sibling_breaking_change_alone_bumps_nothing(self) -> None:
        with SyntheticRepo("keycardai-mcp", "2.1.0") as repo:
            repo.commit("feat(keycardai-oauth)!: multi-resource web-app flow")
            repo.commit("BREAKING CHANGE(keycardai-oauth): resource_url is gone")
            self.assertIsNone(repo.dry_run_version())

    def test_every_package_ignores_a_sibling_breaking_change(self) -> None:
        for package_name in PACKAGES:
            with self.subTest(package=package_name), SyntheticRepo(
                package_name, "1.2.0"
            ) as repo:
                repo.commit("feat(keycardai-somethingelse)!: unrelated rewrite")
                repo.commit(f"feat({package_name}): a feature of its own")
                self.assertEqual(repo.dry_run_version(), "1.3.0")


class ScopedIncrementTests(unittest.TestCase):
    def test_own_breaking_change_majors(self) -> None:
        with SyntheticRepo("keycardai-mcp", "2.1.0") as repo:
            repo.commit("feat(keycardai-mcp)!: drop the sync client")
            self.assertEqual(repo.dry_run_version(), "3.0.0")

    def test_own_breaking_change_footer_majors(self) -> None:
        with SyntheticRepo("keycardai-mcp", "2.1.0") as repo:
            repo.commit(
                "feat(keycardai-mcp): rework the client\n\n"
                "BREAKING CHANGE(keycardai-mcp): the sync client is gone"
            )
            self.assertEqual(repo.dry_run_version(), "3.0.0")

    def test_own_feature_minors(self) -> None:
        with SyntheticRepo("keycardai-mcp", "2.1.0") as repo:
            repo.commit("feat(keycardai-mcp): interrupt-compatible auth mode")
            self.assertEqual(repo.dry_run_version(), "2.2.0")

    def test_own_fix_refactor_and_perf_patch(self) -> None:
        for commit_type in ("fix", "refactor", "perf"):
            with self.subTest(type=commit_type), SyntheticRepo(
                "keycardai-mcp", "2.1.0"
            ) as repo:
                repo.commit(f"{commit_type}(keycardai-mcp): a small change")
                self.assertEqual(repo.dry_run_version(), "2.1.1")

    def test_breaking_change_stays_minor_below_1_0(self) -> None:
        with SyntheticRepo("keycardai-oauth", "0.25.0") as repo:
            repo.commit("feat(keycardai-oauth)!: multi-resource web-app flow")
            self.assertEqual(repo.dry_run_version(), "0.26.0")


class NonReleasableCommitTests(unittest.TestCase):
    """ci/build/test/chore/docs commits must not produce a release."""

    def test_ci_and_build_commits_bump_nothing(self) -> None:
        for commit_type in ("ci", "build", "test", "chore", "docs"):
            with self.subTest(type=commit_type), SyntheticRepo(
                "keycardai-mcp", "2.1.0"
            ) as repo:
                repo.commit(f"{commit_type}(keycardai-mcp): release plumbing")
                self.assertIsNone(repo.dry_run_version())

    def test_ci_commits_are_absent_from_the_changelog(self) -> None:
        # detect-changes derives the release matrix from `cz changelog
        # --dry-run`, so a ci/build commit must leave it empty as well.
        with SyntheticRepo("keycardai-mcp", "2.1.0") as repo:
            repo.commit("ci(keycardai-mcp): never tag before the bump PR merges")
            result = subprocess.run(
                [sys.executable, "-m", "commitizen", "changelog", "--dry-run"],
                cwd=repo.path,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotIn("never tag before", result.stdout)


if __name__ == "__main__":
    unittest.main()
