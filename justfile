# Setup development environment
dev-setup:
    uv run pre-commit install
    uv sync --all-extras --all-packages

# Build the project. packages/fastmcp sits outside the uv workspace (it holds
# mcp<2.0; see ECO-198), so it syncs separately.
build:
    uv sync --all-packages
    cd packages/fastmcp && uv sync --extra test

# Run tests for all packages
test: build
    just test-package oauth
    just test-package starlette
    just test-package mcp
    just test-package fastmcp
    just test-package a2a
    just test-package langchain
    just test-package temporal

# Run tests for a specific package
test-package PACKAGE:
    cd packages/{{PACKAGE}} && uv run --extra test pytest tests/ -v

# Run a specific test file within a package
test-file PACKAGE FILE:
    cd packages/{{PACKAGE}} && uv run --extra test pytest tests/{{FILE}} -v

# Run tests with coverage enforcement. mcp stays at 60%, but the denominator it
# measures grew: 2679 statements against ~2500 before, because all four
# *_agents.py client integrations are now imported by
# tests/.../test_tool_schema_reads.py and so are counted. Dropping the agent
# frameworks alone would have shrunk it to 2036 and inflated the percentage to
# ~75 on less measured code; that test is what keeps the gate honest. Headroom
# is thin (60.88% actual) -- if it dips, add coverage rather than lowering this.
test-coverage: build
    cd packages/oauth && uv run --extra test pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=70
    cd packages/starlette && uv run --extra test pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=55
    cd packages/mcp && uv run --extra test pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=60
    cd packages/fastmcp && uv run --extra test pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=60
    cd packages/a2a && uv run --extra test pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=55
    cd packages/langchain && uv run --extra test pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=85
    cd packages/temporal && uv run --extra test pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=85

check:
    uv run ruff check

fix:
    uv run ruff check --fix

fix-all:
    uv run ruff check --fix --unsafe-fixes


# Run type checker on all files
typecheck:
    uv run --frozen ty check

# Validate commit messages for PR
validate-commits BASE_BRANCH="origin/main":
    uv run python scripts/changelog.py validate {{BASE_BRANCH}}

# Preview changelog changes for each package
preview-changelog BASE_BRANCH="origin/main":
    uv run python scripts/changelog.py preview {{BASE_BRANCH}}

# Alias for changelog preview (referenced in documentation)
changelog-preview BASE_BRANCH="origin/main":
    uv run python scripts/changelog.py preview {{BASE_BRANCH}}

# Preview expected version changes for packages with unreleased changes
preview-versions FORMAT="markdown":
    uv run python scripts/version_preview.py --format {{FORMAT}}

# Test the release tooling: bump plumbing plus per-package increment detection
test-release-tooling:
    uv run python -m unittest discover -s scripts -p 'test_*.py' -v

# Bump version for a specific package
bump-package PACKAGE_NAME PACKAGE_DIR:
    uv run python scripts/bump_package.py {{PACKAGE_NAME}} {{PACKAGE_DIR}}

# Detect packages with unreleased changes
detect-changes:
    uv run python scripts/changelog.py changes --output-format github

# Extract package information from GitHub tag
extract-package TAG:
    uv run python scripts/changelog.py package {{TAG}} --output-format json
