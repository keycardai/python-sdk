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
        ],
    )
    def test_pr_targets_the_release_branch(self, run_command, _stable) -> None:
        pr_number = bump_package.create_bump_pr(
            "bump/release-mcp-v1/keycardai-mcp-1.0.1",
            "release/mcp-v1",
            "keycardai-mcp",
            "1.0.1",
        )
        self.assertEqual(pr_number, 999)
        command = run_command.call_args_list[0][0][0]
        base_index = command.index("--base")
        self.assertEqual(command[base_index + 1], "release/mcp-v1")
        for call in run_command.call_args_list:
            self.assertNotIn("--auto", call[0][0])


BASE = "a" * 40
HEAD = "b" * 40
MOVED = "c" * 40


def refs_api_calls(run_command: mock.Mock) -> list[list[str]]:
    return [
        call[0][0]
        for call in run_command.call_args_list
        if any(arg.startswith("repos/") and "git/refs" in arg for arg in call[0][0])
    ]


class ChecksGateTests(unittest.TestCase):
    """The merge path only opens once every check has concluded successfully."""

    def test_verdict_prefers_failure_over_pending(self) -> None:
        data = {
            "statusCheckRollup": [
                {"status": "IN_PROGRESS", "conclusion": None},
                {"status": "COMPLETED", "conclusion": "FAILURE"},
            ]
        }
        self.assertEqual(bump_package.checks_verdict(data), "FAILURE")

    def test_verdict_is_pending_while_any_check_runs(self) -> None:
        data = {
            "statusCheckRollup": [
                {"status": "COMPLETED", "conclusion": "SUCCESS"},
                {"status": "QUEUED", "conclusion": None},
            ]
        }
        self.assertEqual(bump_package.checks_verdict(data), "PENDING")

    @mock.patch.object(bump_package.time, "sleep")
    @mock.patch.object(bump_package.time, "time", return_value=0)
    @mock.patch.object(bump_package, "run_command")
    def test_failed_check_refuses_without_touching_refs(
        self, run_command, _time, _sleep
    ) -> None:
        run_command.return_value = (
            0,
            json.dumps(
                {
                    "state": "OPEN",
                    "headRefOid": HEAD,
                    "statusCheckRollup": [
                        {"status": "COMPLETED", "conclusion": "FAILURE"}
                    ],
                }
            ),
            "",
        )

        head = bump_package.wait_for_checks("keycardai/python-sdk", 250)

        self.assertIsNone(head)
        self.assertEqual(refs_api_calls(run_command), [])

    @mock.patch.object(bump_package.time, "sleep")
    @mock.patch.object(bump_package.time, "time", side_effect=[0, 10_000])
    @mock.patch.object(bump_package, "run_command")
    def test_pending_checks_time_out_instead_of_passing(
        self, run_command, _time, _sleep
    ) -> None:
        run_command.return_value = (
            0,
            json.dumps(
                {
                    "state": "OPEN",
                    "headRefOid": HEAD,
                    "statusCheckRollup": [
                        {"status": "IN_PROGRESS", "conclusion": None}
                    ],
                }
            ),
            "",
        )

        head = bump_package.wait_for_checks(
            "keycardai/python-sdk", 250, timeout_seconds=1
        )

        self.assertIsNone(head)

    @mock.patch.object(bump_package, "fast_forward_target")
    @mock.patch.object(bump_package, "wait_for_checks", return_value=None)
    def test_merge_is_unreachable_when_checks_gate_refuses(
        self, _checks, fast_forward_target
    ) -> None:
        merge_sha = bump_package.merge_bump_pr(
            "keycardai/python-sdk", 250, "bump/main/keycardai-mcp-2.2.0", "main",
            BASE, ["pyproject.toml"], "bump: keycardai-mcp → 2.2.0", "Auto-bump.",
        )
        self.assertIsNone(merge_sha)
        fast_forward_target.assert_not_called()


class StrictFastForwardTests(unittest.TestCase):
    """The target ref is only ever fast-forwarded from the SHA the bump was built on."""

    @mock.patch.object(bump_package, "verify_pr_merged")
    @mock.patch.object(bump_package, "rebase_bump_branch", return_value=False)
    @mock.patch.object(bump_package, "get_live_branch_sha", return_value=MOVED)
    @mock.patch.object(bump_package, "wait_for_checks", return_value=HEAD)
    @mock.patch.object(bump_package, "run_command")
    def test_guard_refuses_when_target_moved(
        self, run_command, _checks, _live, rebase_bump_branch, verify_pr_merged
    ) -> None:
        merge_sha = bump_package.merge_bump_pr(
            "keycardai/python-sdk", 250, "bump/main/keycardai-mcp-2.2.0", "main",
            BASE, ["pyproject.toml"], "bump: keycardai-mcp → 2.2.0", "Auto-bump.",
        )

        self.assertIsNone(merge_sha)
        self.assertEqual(refs_api_calls(run_command), [])
        rebase_bump_branch.assert_called_once_with(
            "keycardai/python-sdk", "bump/main/keycardai-mcp-2.2.0", BASE, MOVED,
            ["pyproject.toml"], "bump: keycardai-mcp → 2.2.0", "Auto-bump.",
        )
        verify_pr_merged.assert_not_called()

    @mock.patch.object(bump_package, "verify_pr_merged", return_value=HEAD)
    @mock.patch.object(bump_package, "get_live_branch_sha", return_value=BASE)
    @mock.patch.object(bump_package, "wait_for_checks", return_value=HEAD)
    @mock.patch.object(bump_package, "run_command", return_value=(0, "{}", ""))
    def test_fast_forward_never_forces_the_target_ref(
        self, run_command, _checks, _live, _verify
    ) -> None:
        merge_sha = bump_package.merge_bump_pr(
            "keycardai/python-sdk", 250, "bump/main/keycardai-mcp-2.2.0", "main",
            BASE, ["pyproject.toml"], "bump: keycardai-mcp → 2.2.0", "Auto-bump.",
        )

        self.assertEqual(merge_sha, HEAD)
        calls = refs_api_calls(run_command)
        self.assertEqual(len(calls), 1)
        command = calls[0]
        self.assertIn("PATCH", command)
        self.assertIn("repos/keycardai/python-sdk/git/refs/heads/main", command)
        self.assertIn(f"sha={HEAD}", command)
        self.assertFalse(any("force" in arg for arg in command), command)

    @mock.patch.object(bump_package, "create_signed_commit_on_branch", return_value=True)
    @mock.patch.object(bump_package, "run_command")
    def test_rebuild_refuses_when_target_changed_bumped_files(
        self, run_command, create_signed_commit_on_branch
    ) -> None:
        run_command.return_value = (0, "packages/mcp/pyproject.toml\nREADME.md", "")

        rebuilt = bump_package.rebase_bump_branch(
            "keycardai/python-sdk", "bump/main/keycardai-mcp-2.2.0", BASE, MOVED,
            ["packages/mcp/pyproject.toml", "packages/mcp/CHANGELOG.md"],
            "bump: keycardai-mcp → 2.2.0", "Auto-bump.",
        )

        self.assertFalse(rebuilt)
        self.assertEqual(refs_api_calls(run_command), [])
        create_signed_commit_on_branch.assert_not_called()


class MergeVerificationTests(unittest.TestCase):
    """No tag is ever created unless GitHub reports the PR merged at the head."""

    @mock.patch.object(bump_package.time, "sleep")
    @mock.patch.object(bump_package.time, "time", side_effect=[0, 10_000])
    @mock.patch.object(bump_package, "run_command")
    def test_unmerged_pr_after_fast_forward_returns_none(
        self, run_command, _time, _sleep
    ) -> None:
        run_command.return_value = (
            0,
            json.dumps({"state": "OPEN", "mergeCommit": None}),
            "",
        )

        self.assertIsNone(
            bump_package.verify_pr_merged(
                "keycardai/python-sdk", 250, HEAD, timeout_seconds=1
            )
        )

    @mock.patch.object(bump_package, "run_command")
    def test_merge_at_unexpected_sha_returns_none(self, run_command) -> None:
        run_command.return_value = (
            0,
            json.dumps({"state": "MERGED", "mergeCommit": {"oid": MOVED}}),
            "",
        )

        self.assertIsNone(
            bump_package.verify_pr_merged("keycardai/python-sdk", 250, HEAD)
        )

    @mock.patch.object(bump_package, "create_and_push_tag")
    @mock.patch.object(bump_package, "verify_pr_merged", return_value=None)
    @mock.patch.object(bump_package, "fast_forward_target", return_value=True)
    @mock.patch.object(bump_package, "get_live_branch_sha", return_value=BASE)
    @mock.patch.object(bump_package, "wait_for_checks", return_value=HEAD)
    @mock.patch.object(bump_package, "create_bump_pr", return_value=250)
    @mock.patch.object(bump_package, "create_signed_commit_on_branch", return_value=True)
    @mock.patch.object(bump_package, "create_remote_branch", return_value=True)
    @mock.patch.object(bump_package, "get_modified_files", return_value=["pyproject.toml"])
    @mock.patch.object(bump_package, "get_branch_sha", return_value=BASE)
    @mock.patch.object(bump_package, "cz_bump_files_only", return_value="2.2.0")
    @mock.patch.object(bump_package, "recover_untagged_bump", return_value=None)
    @mock.patch.object(bump_package, "get_repo_slug", return_value="keycardai/python-sdk")
    @mock.patch.object(bump_package, "pull_branch", return_value=True)
    @mock.patch.object(bump_package, "configure_git")
    def test_tag_is_unreachable_when_merge_verification_fails(
        self, *mocks, **_kwargs
    ) -> None:
        create_and_push_tag = mocks[-1]

        self.assertFalse(
            bump_package.bump_package("keycardai-mcp", "packages/mcp")
        )
        create_and_push_tag.assert_not_called()

    @mock.patch.object(bump_package, "create_and_push_tag")
    @mock.patch.object(bump_package, "merge_bump_pr", return_value=None)
    @mock.patch.object(bump_package, "create_bump_pr", return_value=250)
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
