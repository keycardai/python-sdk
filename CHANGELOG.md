## 0.3.0-keycardai (2026-06-10)


- build(keycardai): take examples out of the uv workspace (#151)
- Examples are globbed in as workspace members via packages/*/examples/*, so
every root uv operation generates their package metadata. uv 0.11.20 (released
2026-06-10, pulled in by CI's unpinned setup-uv) fails that generation on
Linux during the nested `uv run cz changelog`, breaking the Merge to Main
detect-changes job even though `uv sync` and the PR checks pass. Examples ship
their own lockfiles and only pin their parent package, so they are standalone
projects: exclude them from the workspace and keep their path sources. Root
operations now resolve only the workspace packages.
- Also send changelog.py error output to stderr so a failed detect-changes shows
in the CI log instead of being captured into $(just detect-changes).
- Co-authored-by: GitHub Action <action@github.com>
- build(keycardai): use workspace sources for keycard deps in examples (#150)
- uv 0.11.20 rejects a workspace member overriding a workspace source with a
path. Each example declared keycardai-<pkg> = { path = "../../", editable =
true } while the root workspace already declares { workspace = true }. Switch
the examples to { workspace = true } (uv 0.11.20 guidance). No uv pin; no
lockfile change. Unblocks lint-and-test / validate-commits for all PRs.
- Co-authored-by: GitHub Action <action@github.com>

## 0.2.0-keycardai (2025-09-10)

## 0.1.0-keycardai (2025-09-07)


- feat(keycardai): initial release
