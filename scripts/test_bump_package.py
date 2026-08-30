"""Unit tests for the branch/increment plumbing in bump_package.py.

Mirrors typescript-sdk's scripts/test_bump_package.py so the two release
pipelines stay symmetric. Run with:

    python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import json
import unittest
from unittest import mock

import bump_package


class BumpBranchNameTests(unittest.TestCase):
    def test_main_line_branch_name(self) -> None:
        self.assertEqual(
            bump_package.bump_branch_name("main", "keycardai-mcp", "1.0.1"),
            "bump/main/keycardai-mcp-1.0.1",
        )

    def test_release_line_slashes_are_sanitized(self) -> None:
        self.assertEqual(
            bump_package.bump_branch_name("release/mcp-v1", "keycardai-mcp", "1.0.1"),
            "bump/release-mcp-v1/keycardai-mcp-1.0.1",
        )


class PullBranchTests(unittest.TestCase):
    @mock.patch.object(bump_package, "run_command", return_value=(0, "", ""))
    def test_pull_branch_fetches_and_resets_to_target(self, run_command) -> None:
        self.assertTrue(bump_package.pull_branch("release/mcp-v1"))
        run_command.assert_any_call(
            ["git", "fetch", "origin", "release/mcp-v1"],
        )
        run_command.assert_any_call(
            ["git", "reset", "--hard", "origin/release/mcp-v1"],
        )


class ForcedIncrementTests(unittest.TestCase):
    @mock.patch.object(
        bump_package,
        "run_command",
        return_value=(0, "bump: keycardai-mcp 0.27.0 -> 1.0.0", ""),
    )
    def test_forced_increment_is_forwarded_to_commitizen(self, run_command) -> None:
        version = bump_package.cz_bump_files_only(
            "packages/mcp", "keycardai-mcp", increment="major"
        )
        self.assertEqual(version, "1.0.0")
        command = run_command.call_args[0][0]
        self.assertIn("--increment", command)
        self.assertIn("MAJOR", command)
        self.assertIn("--allow-no-commit", command)

    @mock.patch.object(
        bump_package,
        "run_command",
        return_value=(0, "bump: keycardai-mcp 1.0.0 -> 1.0.1", ""),
    )
    def test_auto_increment_leaves_commitizen_derivation_alone(
        self, run_command
    ) -> None:
        version = bump_package.cz_bump_files_only("packages/mcp", "keycardai-mcp")
        self.assertEqual(version, "1.0.1")
        command = run_command.call_args[0][0]
        self.assertNotIn("--increment", command)
        self.assertNotIn("--allow-no-commit", command)


class PrBaseBranchTests(unittest.TestCase):
    @mock.patch.object(bump_package, "wait_for_pr_stable", return_value=True)
    @mock.patch.object(
        bump_package,
        "run_command",
        side_effect=[
            # gh pr create
            (0, "https://github.com/keycardai/python-sdk/pull/999", ""),
            # gh pr merge --auto --squash
            (0, "", ""),
        ],
    )
    def test_pr_targets_the_release_branch(self, run_command, _stable) -> None:
        pr_number = bump_package.create_pr_with_automerge(
            "bump/release-mcp-v1/keycardai-mcp-1.0.1",
            "release/mcp-v1",
            "keycardai-mcp",
            "1.0.1",
        )
        self.assertEqual(pr_number, 999)
        command = run_command.call_args_list[0][0][0]
        base_index = command.index("--base")
        self.assertEqual(command[base_index + 1], "release/mcp-v1")


class MergeRefusalTests(unittest.TestCase):
    """A refused merge must fail the run rather than force the release."""

    PR_STATUS = json.dumps(
        {
            "state": "OPEN",
            "mergeCommit": None,
            "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"}],
        }
    )

    @mock.patch.object(bump_package.time, "sleep")
    @mock.patch.object(bump_package.time, "time", side_effect=[0, 0, 10, 10_000])
    @mock.patch.object(bump_package, "run_command")
    def test_refused_merge_never_updates_the_target_ref(
        self, run_command, _time, _sleep
    ) -> None:
        run_command.side_effect = [
            (0, self.PR_STATUS, ""),
            (1, "", "the base branch policy prohibits the merge"),
        ]

        merge_sha = bump_package.wait_for_pr_merge(
            "keycardai/python-sdk", 250, "main", timeout_seconds=1
        )

        self.assertIsNone(merge_sha)
        for call in run_command.call_args_list:
            command = call[0][0]
            self.assertNotIn("PATCH", command)
            self.assertFalse(
                any(arg.startswith("repos/") and "git/refs" in arg for arg in command),
                f"the refs API must not be touched: {command}",
            )

    @mock.patch.object(bump_package, "create_and_push_tag")
    @mock.patch.object(bump_package, "wait_for_pr_merge", return_value=None)
    @mock.patch.object(bump_package, "create_pr_with_automerge", return_value=250)
    @mock.patch.object(bump_package, "create_signed_commit_on_branch", return_value=True)
    @mock.patch.object(bump_package, "create_remote_branch", return_value=True)
    @mock.patch.object(bump_package, "get_modified_files", return_value=["pyproject.toml"])
    @mock.patch.object(bump_package, "get_branch_sha", return_value="a" * 40)
    @mock.patch.object(bump_package, "cz_bump_files_only", return_value="2.2.0")
    @mock.patch.object(bump_package, "recover_untagged_bump", return_value=None)
    @mock.patch.object(bump_package, "get_repo_slug", return_value="keycardai/python-sdk")
    @mock.patch.object(bump_package, "pull_branch", return_value=True)
    @mock.patch.object(bump_package, "configure_git")
    def test_unmerged_bump_pr_fails_the_run_without_tagging(
        self, *mocks, **_kwargs
    ) -> None:
        create_and_push_tag = mocks[-1]

        self.assertFalse(
            bump_package.bump_package("keycardai-mcp", "packages/mcp")
        )
        create_and_push_tag.assert_not_called()


if __name__ == "__main__":
    unittest.main()
