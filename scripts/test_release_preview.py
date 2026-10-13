"""Unit tests for the release preview formatter and the PR comment plumbing.

Run with:

    python3 -m unittest discover -s scripts -p 'test_*.py'
"""

import unittest

import pr_comment
import release_preview
from release_preview import (
    PackageRelease,
    Preview,
    PreviewError,
    cap_text,
    parse_bump_dry_run,
    render_preview,
)

WARNINGS = (
    "Multiple config files detected: pyproject.toml, /repo/pyproject.toml. "
    "Using config file: 'pyproject.toml'.\n"
    "Invalid version tag: '0.3.0-keycardai' does not match any configured tag format\n"
)

OAUTH_BUMP = """bump: keycardai-oauth 0.31.0 → 0.31.1
tag to create: 0.31.1-keycardai-oauth
increment detected: PATCH

## 0.31.1-keycardai-oauth (2026-10-13)


- fix(keycardai-oauth): build the zone JWKS URL without mutating AnyHttpUrl
- the second line of the entry
"""

MCP_BUMP = """bump: keycardai-mcp 2.3.2 → 2.4.0
tag to create: 2.4.0-keycardai-mcp
increment detected: MINOR

## 2.4.0-keycardai-mcp (2026-10-13)


- feat(keycardai-mcp): typed accessor for the verified caller
"""


class ParseBumpDryRunTests(unittest.TestCase):
    def test_release_with_entries(self) -> None:
        release = parse_bump_dry_run("keycardai-oauth", 0, OAUTH_BUMP, WARNINGS)
        self.assertIsInstance(release, PackageRelease)
        self.assertEqual(release.current_version, "0.31.0")
        self.assertEqual(release.next_version, "0.31.1")
        self.assertEqual(release.increment, "patch")
        self.assertEqual(
            release.entries,
            [
                "- fix(keycardai-oauth): build the zone JWKS URL without mutating AnyHttpUrl",
                "- the second line of the entry",
            ],
        )

    def test_no_commits_to_bump_is_not_a_release(self) -> None:
        stdout = "bump: keycardai-a2a 0.5.0 → 0.5.0\ntag to create: 0.5.0-keycardai-a2a\n"
        stderr = WARNINGS + "[NO_COMMITS_TO_BUMP]\nThe commits found are not eligible to be bumped"
        self.assertIsNone(parse_bump_dry_run("keycardai-a2a", 21, stdout, stderr))

    def test_no_commits_found_is_not_a_release(self) -> None:
        stderr = WARNINGS + "[NO_COMMITS_FOUND]\nNo new commits found."
        self.assertIsNone(parse_bump_dry_run("keycardai-a2a", 3, "", stderr))

    def test_other_failure_keeps_the_last_real_line_only(self) -> None:
        stderr = WARNINGS + "[NO_VERSION_SPECIFIED]\nCheck if current version is specified in config file"
        error = parse_bump_dry_run("keycardai-a2a", 7, "", stderr)
        self.assertIsInstance(error, PreviewError)
        self.assertEqual(error.message, "Check if current version is specified in config file")
        self.assertNotIn("Multiple config files", error.message)


class RenderPreviewTests(unittest.TestCase):
    def test_two_package_release(self) -> None:
        preview = Preview(
            releases=[
                parse_bump_dry_run("keycardai-oauth", 0, OAUTH_BUMP, WARNINGS),
                parse_bump_dry_run("keycardai-mcp", 0, MCP_BUMP, WARNINGS),
            ],
            skipped=["keycardai-a2a", "keycardai-starlette"],
        )
        text = render_preview(preview)
        self.assertEqual(
            text,
            "## Release preview\n"
            "\n"
            "**keycardai-oauth** 0.31.0 → 0.31.1 (patch)\n"
            "\n"
            "- fix(keycardai-oauth): build the zone JWKS URL without mutating AnyHttpUrl\n"
            "- the second line of the entry\n"
            "\n"
            "**keycardai-mcp** 2.3.2 → 2.4.0 (minor)\n"
            "\n"
            "- feat(keycardai-mcp): typed accessor for the verified caller\n"
            "\n"
            "No other package releases.",
        )

    def test_no_release_package_is_one_quiet_line_never_stderr(self) -> None:
        preview = Preview(releases=[], skipped=["keycardai-a2a", "keycardai-oauth"])
        text = render_preview(preview)
        self.assertEqual(text, "## Release preview\n\nThis PR releases nothing.")
        self.assertNotIn("NO_COMMITS_TO_BUMP", text)
        self.assertNotIn("keycardai-a2a", text)

    def test_config_file_warning_never_appears(self) -> None:
        preview = Preview(
            releases=[parse_bump_dry_run("keycardai-oauth", 0, OAUTH_BUMP, WARNINGS)],
            skipped=["keycardai-a2a"],
        )
        text = render_preview(preview)
        self.assertNotIn("Multiple config files detected", text)
        self.assertNotIn("Invalid version tag", text)
        self.assertNotIn("Error", text)

    def test_real_error_is_one_line(self) -> None:
        preview = Preview(errors=[PreviewError("keycardai-a2a", "boom")])
        text = render_preview(preview)
        self.assertIn("**keycardai-a2a**: could not compute a preview (boom)", text)


class CapTextTests(unittest.TestCase):
    def test_short_text_is_unchanged(self) -> None:
        self.assertEqual(cap_text("short", 100), "short")

    def test_long_text_is_cut_on_a_line_with_a_pointer_to_the_summary(self) -> None:
        text = "\n".join(f"- entry {i}" for i in range(500))
        capped = cap_text(text, 2_000, "https://example.test/runs/1")
        self.assertLessEqual(len(capped), 2_000)
        self.assertTrue(capped.endswith("(https://example.test/runs/1)."))
        self.assertIn("Preview cut at 2000 characters", capped)
        body = capped.split("\n\nPreview cut")[0]
        self.assertTrue(body.endswith(body.splitlines()[-1]))
        self.assertTrue(all(line.startswith("- entry ") for line in body.splitlines()))

    def test_comment_cap_is_well_under_the_github_limit(self) -> None:
        self.assertLess(pr_comment.MAX_COMMENT_CHARS, 65_536 // 2)


class ExistingCommentTests(unittest.TestCase):
    def test_finds_the_numeric_id_by_signature(self) -> None:
        comments = [
            {"id": 1, "body": "unrelated"},
            {"id": 5220651411, "body": f"## Release preview\n\n*{release_preview.SIGNATURE}.*"},
        ]
        self.assertEqual(pr_comment.find_existing_comment(comments), 5220651411)

    def test_no_signature_means_no_comment(self) -> None:
        self.assertIsNone(pr_comment.find_existing_comment([{"id": 1, "body": "hello"}]))

    def test_actions(self) -> None:
        self.assertEqual(pr_comment.decide_action(True, None), "create")
        self.assertEqual(pr_comment.decide_action(True, 42), "update")
        self.assertEqual(pr_comment.decide_action(False, 42), "update")
        self.assertEqual(pr_comment.decide_action(False, None), "skip")


if __name__ == "__main__":
    unittest.main()
