# AGENTS.md

Context for coding agents working in keycardai/python-sdk. CLAUDE.md points here.

## What this repo is

A uv workspace of independently versioned PyPI packages under `packages/`, all in the `keycardai.*` namespace. Python >= 3.10 (CI tests 3.10 through 3.13, subject to each package's minimum). The root `keycardai` project has no source; it only hosts the workspace and shared tool config.

Packages (dependency direction in parentheses):

- `packages/oauth` (`keycardai-oauth`): OAuth 2.0 client, server-side verification, application credentials. Depends on no sibling.
- `packages/starlette` (`keycardai-starlette`): Starlette/FastAPI middleware and route builders (depends on oauth).
- `packages/mcp` (`keycardai-mcp`): MCP server auth, `@grant`, MCP client (depends on oauth, starlette). Past 1.0, on `mcp>=2.0`.
- `packages/a2a` (`keycardai-a2a`): agent-to-agent token exchange, service hosting, discovery (depends on oauth, starlette).
- `packages/langchain` (`keycardai-langchain`): LangChain integration (depends on oauth).
- `packages/temporal` (`keycardai-temporal`): per-activity token minting for Temporal (depends on oauth).
- `packages/fastmcp` (`keycardai-fastmcp`): FastMCP 3.x integration (depends on oauth). Outside the workspace.
- `packages/pydantic-ai` (`keycardai-pydantic-ai`): Pydantic AI integration (depends on oauth). Outside the workspace.
- `packages/hermes` (`keycardai-hermes`): Hermes Agent plugin (depends on oauth). Outside the workspace.

Workspace layout gotchas:

- `fastmcp`, `pydantic-ai` and `hermes` are excluded from `[tool.uv.workspace]` (fastmcp pins `mcp<2.0`; the other two need Python 3.11). Each syncs and locks on its own and path-links `keycardai-oauth` (`../oauth`). They are still released from here via `[tool.keycardai.release] standalone-members`.
- Workspace members are listed explicitly. A new package must be added to `members`, `[tool.uv.sources]`, the ty `root` list, the `justfile` test/coverage/typecheck recipes, the `sibling-floors` matrix in `pr.yml`, and the tag list in `release.yml`.
- Examples under `packages/*/examples/*` are standalone projects with their own lockfiles.

## Setup, build, test, lint, typecheck

Tooling: `uv` and `just`. In `.github/workflows/pr.yml`, the `lint` job runs these commands on Python 3.12:

```sh
uv sync --all-extras
just check                 # uv run ruff check
just typecheck             # ty (pinned ty==0.0.81) over every package's src/
just test-release-tooling  # unittest over scripts/test_*.py
```

The `test` matrix runs `just test-coverage` on each Python version with per-package coverage gates. The `lint-and-test` check requires both jobs to succeed.

Local helpers:

```sh
just dev-setup                  # pre-commit install (commitizen commit-msg hook) + uv sync --all-extras --all-packages
just build                      # uv sync --all-packages, then syncs fastmcp, pydantic-ai, hermes separately
just test                       # all packages
just test-package oauth         # cd packages/oauth && uv run --extra test pytest tests/ -v
just test-file oauth <file>
just fix                        # ruff check --fix
```

Other PR checks:

- `validate-commits`: `just validate-commits` (commitizen over the PR range; skipped for bot PRs).
- `sibling-floors`: builds each dependent package's wheel and runs its tests with each `keycardai-*` dependency at its declared floor from PyPI (`scripts/check_sibling_floors.py`). If you raise a sibling floor to a version not on PyPI yet, the PR needs the `floors-bootstrap` label.
- `langchain-version-matrix`: langchain at lowest-direct floors, a customer pin set, and an mcp 1.x/2.x resolution check.
- Coverage gates are per package in `justfile` (oauth 70, starlette 55, mcp 60, fastmcp 60, a2a 55, langchain/temporal/pydantic-ai/hermes 85). mcp headroom is thin; add tests rather than lowering a gate.

Style: ruff (`E,W,F,I,B,C4,UP`), line length 88, target py310. Typecheck covers `src/` only.

## Commits and releases

Conventional commits, `type(scope): description`. Squash merges, so the PR title is the commit main sees.

- Releasing scopes are the full package name: `keycardai-oauth`, `keycardai-starlette`, `keycardai-mcp`, `keycardai-a2a`, `keycardai-langchain`, `keycardai-temporal`, `keycardai-fastmcp`, `keycardai-pydantic-ai`, `keycardai-hermes`, and `keycardai` for the root project.
- A short scope like `feat(oauth):` matches no package config and releases nothing, with no error. Other scopes seen in history: `deps`, `ci`, or no scope; they do not release.
- Each package's `[tool.commitizen.customize]` matches only its own scope. Increments: `feat` minor; `fix`, `refactor`, `perf`, `revert` patch.
- Breaking change: `!` after the scope (`feat(keycardai-oauth)!: ...`) or a scoped footer `BREAKING CHANGE(keycardai-oauth): ...`. An unscoped `BREAKING CHANGE:` footer does not match the package's bump pattern. Packages with `major_version_zero = true` (everything except `keycardai-mcp`) land a breaking change as a minor.
- Merge to `main` (or `release/mcp-v1`) runs `main.yml`: detects packages with unreleased scoped commits and calls `bump-package.yml` per package. `scripts/bump_package.py` opens a signed bump PR from the release app, merges it, and tags `<version>-<package>` (e.g. `0.37.1-keycardai-oauth`). The tag triggers `release.yml`, which builds by path and `uv publish`es to PyPI.
- Never hand-edit versions (`[tool.commitizen] version`, tags) or `CHANGELOG.md`; the bump job owns them.
- Note: `docs/project/playbooks/release-process.md` still describes a manual `gh workflow run bump-package.yml` trigger and a shorter scope list. The workflows above are what actually runs.

## Spec first

- Behavior changes start as a PR in keycardai/keycard-sdk-spec (`specs/<area>/<name>.md`, with a Unit Tests table of rows and a Divergences section). Implement against the merged spec's rows.
- Existing precedent: `packages/oauth/tests/keycardai/oauth/test_device_authorization.py` names tests `test_row_<n>_...` after the spec's unit test table; `server/test_workload_identity.py` cites its spec in the module docstring. Follow the row-naming form for new spec-driven tests.

## Testing rule

- Package tests, CI and examples are hermetic only: no live Keycard zone. Mock with the existing fixtures (e.g. `client_factory` injection, `packages/mcp/tests/fixtures/`). keycardai/templates' eval owns live-zone coverage.
- `packages/mcp/tests/integration/interactive/` holds older real-zone tests gated behind `RUN_INTERACTIVE_TESTS=1` and `KEYCARD_ZONE_URL`. Do not add to them or wire them into CI.
- `oauth`, `mcp` and `fastmcp` have a `tests/test_examples.py` that imports every example; keep examples importable without network.

## Idioms and gotchas

- Sync/async split (`docs/standards/sync_async_api_standard.mdx`): separate `Client` and `AsyncClient` classes, shared I/O-free private core, no `_async` method suffixes. `keycardai.oauth` follows this.
- Errors: `keycardai.oauth.exceptions` roots everything at `OAuthError` (`OAuthProtocolError`, `TokenExchangeError`, `NetworkError`, `ConfigError`, ...). Raise or subclass these rather than bare exceptions.
- `AccessContext` from `@grant` is non-throwing: callers check `.has_errors()`.
- Minimal dependencies per package (ADR-0002): put framework deps only in the package that needs them, and keep `keycardai-oauth` free of framework deps. Most deps carry no upper bound (README).
- Follow existing patterns before adding new ones (`docs/standards/keep_it_simple.mdx`); comments explain why, not what (`docs/standards/comments_guide.mdx`).
- `ty` is pinned exactly (`ty==0.0.81`); bump it deliberately.
- Versions come from git tags via `uv-dynamic-versioning`; CI jobs that need real versions check out with `fetch-depth: 0`.
