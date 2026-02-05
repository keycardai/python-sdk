# Setup development environment
dev-setup:
    uv run pre-commit install
    uv sync --all-extras --all-packages

# Build the project
build:
    uv sync --all-packages

# Run tests for all packages
test: build
    just test-package oauth
    just test-package mcp
    just test-package mcp-fastmcp

# Run tests for a specific package
test-package PACKAGE:
    cd packages/{{PACKAGE}} && uv run --extra test pytest tests/ -v

# Run a specific test file within a package
test-file PACKAGE FILE:
    cd packages/{{PACKAGE}} && uv run --extra test pytest tests/{{FILE}} -v

# Run tests with coverage enforcement
# Note: mcp package has lower threshold due to optional client integrations (CrewAI, LangChain, etc.)
test-coverage: build
    cd packages/oauth && uv run --extra test pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=70
    cd packages/mcp && uv run --extra test pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=65
    cd packages/mcp-fastmcp && uv run --extra test pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=70

check:
    uv run ruff check

fix:
    uv run ruff check --fix

fix-all:
    uv run ruff check --fix --unsafe-fixes


# Run type checker on all files
typecheck:
    uv run --frozen ty check

docs:
    cd docs && npx --yes mint@latest dev

# Generate API reference documentation for all modules
sdk-ref-all:
    just sdk-ref-mcp-fastmcp
    just sdk-ref-mcp
    just sdk-ref-oauth

sdk-ref-mcp-fastmcp:
    cd packages/mcp-fastmcp && uvx --with-editable . --refresh-package mdxify mdxify@latest keycardai.mcp.integrations.fastmcp --root-module keycardai --anchor-name "Python SDK" --output-dir ../../docs/sdk
sdk-ref-mcp:
    cd packages/mcp && uvx --with-editable . --refresh-package mdxify mdxify@latest keycardai.mcp --root-module keycardai --anchor-name "Python SDK" --output-dir ../../docs/sdk
sdk-ref-oauth:
    cd packages/oauth && uvx --with-editable . --refresh-package mdxify mdxify@latest keycardai.oauth --root-module keycardai --anchor-name "Python SDK" --output-dir ../../docs/sdk


# Clean up API reference documentation
sdk-ref-clean:
    rm -rf docs/sdk

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

# Bump version for a specific package
bump-package PACKAGE_NAME PACKAGE_DIR:
    uv run python scripts/bump_package.py {{PACKAGE_NAME}} {{PACKAGE_DIR}}

# Detect packages with unreleased changes
detect-changes:
    uv run python scripts/changelog.py changes --output-format github

# Extract package information from GitHub tag
extract-package TAG:
    uv run python scripts/changelog.py package {{TAG}} --output-format json
