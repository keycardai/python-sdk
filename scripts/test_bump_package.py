"""Unit tests for the branch/increment plumbing in bump_package.py.

Mirrors typescript-sdk's scripts/test_bump_package.py so the two release
pipelines stay symmetric. Run with:

    python3 -m unittest discover -s scripts -p 'test_*.py'
"""

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


if __name__ == "__main__":
    unittest.main()
