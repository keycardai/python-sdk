"""Unit tests for the ahead-of-PyPI handling in check_sibling_floors.py.

Same style as test_bump_package.py. Run with:

    python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import contextlib
import io
import unittest

import bump_package
import check_sibling_floors

RELEASE_APP = check_sibling_floors.DEFAULT_RELEASE_APP_LOGIN
FLOORS = {"keycardai-oauth": "0.29.0", "keycardai-starlette": "0.9.0"}


def _exists_except(*missing: tuple[str, str]):
    def exists(sibling: str, version: str) -> bool:
        return (sibling, version) not in missing

    return exists


class ParseBumpPrTests(unittest.TestCase):
    def test_release_app_on_bump_branch_is_a_bump_pr(self) -> None:
        branch = bump_package.bump_branch_name("main", "keycardai-oauth", "0.29.0")
        self.assertEqual(
            check_sibling_floors.parse_bump_pr(branch, RELEASE_APP, RELEASE_APP),
            ("keycardai-oauth", "0.29.0"),
        )

    def test_release_line_branch_is_a_bump_pr(self) -> None:
        branch = bump_package.bump_branch_name(
            "release/mcp-v1", "keycardai-mcp", "1.0.1"
        )
        self.assertEqual(
            check_sibling_floors.parse_bump_pr(branch, RELEASE_APP, RELEASE_APP),
            ("keycardai-mcp", "1.0.1"),
        )

    def test_bump_branch_from_a_human_is_not_a_bump_pr(self) -> None:
        branch = bump_package.bump_branch_name("main", "keycardai-oauth", "0.29.0")
        self.assertIsNone(
            check_sibling_floors.parse_bump_pr(branch, "larry", RELEASE_APP)
        )

    def test_release_app_on_another_branch_is_not_a_bump_pr(self) -> None:
        self.assertIsNone(
            check_sibling_floors.parse_bump_pr(
                "devin/123-something", RELEASE_APP, RELEASE_APP
            )
        )


class AheadOfPypiTests(unittest.TestCase):
    def _run(
        self, *, bootstrap: bool, bump_pr, missing
    ) -> tuple[bool | None, int | None, str]:
        out = io.StringIO()
        result = None
        code = None
        with contextlib.redirect_stdout(out):
            try:
                result = check_sibling_floors.check_ahead_of_pypi(
                    "keycardai-fastmcp",
                    FLOORS,
                    bootstrap=bootstrap,
                    bump_pr=bump_pr,
                    exists=_exists_except(*missing),
                )
            except SystemExit as e:
                code = e.code
        return result, code, out.getvalue()

    def test_all_floors_published_proceeds(self) -> None:
        result, code, out = self._run(bootstrap=False, bump_pr=None, missing=[])
        self.assertTrue(result)
        self.assertIsNone(code)
        self.assertEqual(out, "")

    def test_bump_pr_releasing_the_missing_version_skips(self) -> None:
        result, code, out = self._run(
            bootstrap=False,
            bump_pr=("keycardai-oauth", "0.29.0"),
            missing=[("keycardai-oauth", "0.29.0")],
        )
        self.assertFalse(result)
        self.assertIsNone(code)
        self.assertIn(
            "floor keycardai-oauth>=0.29.0 is the version this bump PR releases", out
        )
        self.assertNotIn("::error::", out)
        self.assertNotIn("floors-bootstrap", out)

    def test_bump_pr_with_a_different_missing_version_fails(self) -> None:
        result, code, out = self._run(
            bootstrap=False,
            bump_pr=("keycardai-oauth", "0.30.0"),
            missing=[("keycardai-oauth", "0.29.0")],
        )
        self.assertIsNone(result)
        self.assertEqual(code, 1)
        self.assertIn(
            "::error::keycardai-fastmcp: floor keycardai-oauth>=0.29.0 is ahead of PyPI",
            out,
        )
        self.assertNotIn("this bump PR releases", out)

    def test_bump_pr_does_not_cover_another_missing_sibling(self) -> None:
        result, code, out = self._run(
            bootstrap=False,
            bump_pr=("keycardai-oauth", "0.29.0"),
            missing=[("keycardai-oauth", "0.29.0"), ("keycardai-starlette", "0.9.0")],
        )
        self.assertIsNone(result)
        self.assertEqual(code, 1)
        self.assertIn(
            "::error::keycardai-fastmcp: floor keycardai-starlette>=0.9.0 is ahead of PyPI",
            out,
        )

    def test_plain_pr_with_missing_version_and_no_label_fails(self) -> None:
        result, code, out = self._run(
            bootstrap=False, bump_pr=None, missing=[("keycardai-oauth", "0.29.0")]
        )
        self.assertIsNone(result)
        self.assertEqual(code, 1)
        self.assertIn(
            "::error::keycardai-fastmcp: floor keycardai-oauth>=0.29.0 is ahead of PyPI",
            out,
        )
        self.assertIn("add the floors-bootstrap label", out)

    def test_floors_bootstrap_label_still_skips_a_feature_pr(self) -> None:
        result, code, out = self._run(
            bootstrap=True, bump_pr=None, missing=[("keycardai-oauth", "0.29.0")]
        )
        self.assertFalse(result)
        self.assertIsNone(code)
        self.assertIn(
            "::warning::keycardai-fastmcp: floor keycardai-oauth>=0.29.0 is ahead of PyPI",
            out,
        )
        self.assertIn("floors-bootstrap label is set on this PR", out)


if __name__ == "__main__":
    unittest.main()
