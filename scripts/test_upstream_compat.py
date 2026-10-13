"""Unit tests for scripts/upstream_compat.py (SDK-22)."""

from __future__ import annotations

import unittest

import upstream_compat as uc

FASTMCP = uc.Entry("fastmcp", "fastmcp", "project.dependencies")
MCP = uc.Entry("mcp", "mcp", "project.dependencies")
AGENTS = uc.Entry(
    "temporal", "openai-agents", "project.optional-dependencies.openai-agents"
)
TEMPORALIO = uc.Entry("temporal", "temporalio", "project.dependencies")

PYPROJECT = """[project]
name = "keycardai-temporal"
dependencies = [
    "keycardai-oauth>=0.33.0",
    "temporalio>=1.32.0",
]

[project.optional-dependencies]
openai-agents = [
    "openai-agents>=0.20.0",
    "temporalio[openai-agents]>=1.32.0",
]
test = [
    "pytest>=8.0",
]

[project.urls]
Homepage = "https://keycard.ai"
"""


class SpecifierTests(unittest.TestCase):
    def test_open_floor_covers_every_later_release(self) -> None:
        self.assertTrue(uc.in_range(">=3.1.0", "4.1.0"))
        self.assertFalse(uc.has_cap(">=3.1.0"))
        self.assertEqual(uc.widened_range(">=3.1.0", "4.1.0"), ">=3.1.0")
        self.assertEqual(uc.decide(True, uc.in_range(">=3.1.0", "4.1.0")), "record")

    def test_capped_same_major_moves_the_cap(self) -> None:
        self.assertTrue(uc.in_range(">=2.0.0,<3.0", "2.3.0"))
        self.assertFalse(uc.in_range(">=1.2.0,<1.9.0", "1.9.3"))
        self.assertEqual(uc.widened_range(">=1.2.0,<1.9.0", "1.9.3"), ">=1.2.0,<2.0")
        self.assertFalse(uc.is_new_major(">=1.2.0,<1.9.0", "1.9.3"))

    def test_capped_new_major_starts_a_new_line(self) -> None:
        self.assertFalse(uc.in_range(">=2.0.0,<3.0", "3.1.0"))
        self.assertEqual(uc.widened_range(">=2.0.0,<3.0", "3.1.0"), ">=3.1.0,<4.0")
        self.assertTrue(uc.is_new_major(">=2.0.0,<3.0", "3.1.0"))
        self.assertIn(
            "breaking", uc.pr_body(MCP, "3.1.0", ">=2.0.0,<3.0", ">=3.1.0,<4.0", "")
        )
        self.assertNotIn(
            "breaking", uc.pr_body(MCP, "1.9.3", ">=1.2.0,<1.9.0", ">=1.2.0,<2.0", "")
        )

    def test_zero_x_treats_each_minor_as_breaking(self) -> None:
        self.assertFalse(uc.in_range(">=0.20.0,<0.21.0", "0.23.1"))
        self.assertEqual(
            uc.widened_range(">=0.20.0,<0.21.0", "0.23.1"), ">=0.23.1,<0.24.0"
        )
        self.assertTrue(uc.is_new_major(">=0.20.0,<0.21.0", "0.23.1"))
        self.assertEqual(
            uc.widened_range(">=0.23.0,<0.23.1", "0.23.2"), ">=0.23.0,<0.24.0"
        )
        self.assertTrue(uc.in_range(">=0.20.0", "0.23.1"))

    def test_other_comparators_are_kept(self) -> None:
        self.assertEqual(
            uc.widened_range(">=2.0.0,!=2.1.0,<3.0", "3.0.2"), ">=3.0.2,<4.0,!=2.1.0"
        )

    def test_prereleases_and_yanked_files_are_not_latest(self) -> None:
        releases = {
            "4.1.0": [{"yanked": False}],
            "4.2.0rc1": [{"yanked": False}],
            "4.2.0.dev1": [{"yanked": False}],
            "4.1.1": [{"yanked": True}],
            "3.9.0": [{"yanked": False}],
            "garbage": [{"yanked": False}],
        }
        self.assertEqual(uc.latest_stable(releases), "4.1.0")

    def test_decision_matrix(self) -> None:
        self.assertEqual(uc.decide(True, True), "record")
        self.assertEqual(uc.decide(True, False), "pr")
        self.assertEqual(uc.decide(False, True), "issue")
        self.assertEqual(uc.decide(False, False), "issue")

    def test_issue_body_says_whether_users_are_affected(self) -> None:
        inside = uc.issue_body(FASTMCP, "4.1.0", ">=3.1.0", True, "E   boom", "")
        self.assertIn(
            "users installing keycardai-fastmcp today already get this", inside
        )
        outside = uc.issue_body(MCP, "3.1.0", ">=2.0.0,<3.0", False, "E   boom", "")
        self.assertIn("users are not affected until the specifier widens", outside)


class PyprojectTests(unittest.TestCase):
    def test_reads_dependencies_and_extras(self) -> None:
        self.assertEqual(uc.read_declared(PYPROJECT, "temporalio", None), ">=1.32.0")
        self.assertEqual(
            uc.read_declared(PYPROJECT, "openai-agents", "openai-agents"), ">=0.20.0"
        )
        self.assertEqual(AGENTS.extra, "openai-agents")
        self.assertIsNone(TEMPORALIO.extra)

    def test_replace_touches_only_the_named_declaration(self) -> None:
        out = uc.replace_declared(
            PYPROJECT, "openai-agents", "openai-agents", ">=0.23.1,<0.24.0"
        )
        self.assertIn('"openai-agents>=0.23.1,<0.24.0",', out)
        self.assertIn('"temporalio[openai-agents]>=1.32.0",', out)
        self.assertIn('"temporalio>=1.32.0",', out)
        self.assertEqual(len(out.splitlines()), len(PYPROJECT.splitlines()))
        out2 = uc.replace_declared(PYPROJECT, "temporalio", None, ">=1.34.0")
        self.assertIn('"temporalio>=1.34.0",', out2)
        self.assertIn('"temporalio[openai-agents]>=1.32.0",', out2)


class DedupeTests(unittest.TestCase):
    def test_names(self) -> None:
        self.assertEqual(
            uc.branch_name(AGENTS, "0.23.1"), "upstream/temporal-openai-agents-0.23.1"
        )
        self.assertEqual(
            uc.pr_title(MCP, "3.1.0"), "fix(keycardai-mcp): support mcp 3.1.0"
        )
        self.assertEqual(
            uc.issue_title(FASTMCP, "4.1.0"), "fastmcp 4.1.0 breaks keycardai-fastmcp"
        )
        self.assertEqual(AGENTS.key, "temporal:openai-agents")

    def test_pick_entry_by_key_or_unique_package(self) -> None:
        entries = [FASTMCP, MCP, TEMPORALIO, AGENTS]
        self.assertIs(uc.pick_entry(entries, "fastmcp"), FASTMCP)
        self.assertIs(uc.pick_entry(entries, "temporal:openai-agents"), AGENTS)
        with self.assertRaises(SystemExit):
            uc.pick_entry(entries, "temporal")
        with self.assertRaises(SystemExit):
            uc.pick_entry(entries, "langchain")

    def test_exact_title_match_only(self) -> None:
        items = [
            {"number": 7, "title": "fastmcp 4.1.0 breaks keycardai-fastmcp"},
            {"number": 8, "title": "fastmcp 4.1.1 breaks keycardai-fastmcp"},
        ]
        self.assertEqual(
            uc.find_by_title(items, "fastmcp 4.1.0 breaks keycardai-fastmcp"), 7
        )
        self.assertIsNone(
            uc.find_by_title(items, "fastmcp 4.2.0 breaks keycardai-fastmcp")
        )

    def test_failed_differently(self) -> None:
        body = uc.issue_body(
            FASTMCP, "4.1.0", ">=3.1.0", True, "E   ImportError: x", ""
        )
        self.assertFalse(uc.failed_differently(body, "E   ImportError: x"))
        self.assertTrue(uc.failed_differently(body, "E   TypeError: y"))

    def test_already_tested(self) -> None:
        state = {"fastmcp:fastmcp": {"version": "4.1.0", "run": "r"}}
        self.assertTrue(uc.already_tested(state, FASTMCP, "4.1.0"))
        self.assertFalse(uc.already_tested(state, FASTMCP, "4.1.1"))
        self.assertFalse(uc.already_tested(state, MCP, "4.1.0"))


class StateWriteTests(unittest.TestCase):
    def test_merge_by_key_keeps_the_other_entries(self) -> None:
        start = {"mcp:mcp": {"version": "2.3.0", "run": "r1"}}
        a = uc.merge_state(start, "fastmcp:fastmcp", {"version": "4.1.0", "run": "r2"})
        b = uc.merge_state(
            start, "temporal:temporalio", {"version": "1.34.0", "run": "r3"}
        )
        self.assertEqual(set(a), {"mcp:mcp", "fastmcp:fastmcp"})
        self.assertEqual(set(b), {"mcp:mcp", "temporal:temporalio"})
        self.assertEqual(start, {"mcp:mcp": {"version": "2.3.0", "run": "r1"}})

    def test_rejected_push_rereads_the_tip_and_retries_once(self) -> None:
        tips = iter([{}, {"mcp:mcp": {"version": "2.3.0", "run": "r1"}}])
        results = iter([False, True])
        writes: list[dict] = []
        sleeps: list[float] = []

        def write_once(repo_root: str, branch: str, state: dict, message: str) -> bool:
            writes.append(state)
            return next(results)

        final = uc.record_state(
            "/repo",
            "upstream-compat-state",
            "fastmcp:fastmcp",
            {"version": "4.1.0", "run": "r2"},
            "msg",
            read=lambda root, branch: next(tips),
            write_once=write_once,
            sleep=sleeps.append,
        )
        self.assertEqual(len(writes), 2)
        self.assertEqual(set(final), {"mcp:mcp", "fastmcp:fastmcp"})
        self.assertEqual(len(sleeps), 1)

    def test_exhausted_attempts_fail_as_a_state_write(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            uc.record_state(
                "/repo",
                "upstream-compat-state",
                "mcp:mcp",
                {"version": "2.3.0", "run": ""},
                "msg",
                attempts=3,
                read=lambda root, branch: {},
                write_once=lambda *a: False,
                sleep=lambda s: None,
            )
        self.assertIn("state write failed after 3 attempts", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
