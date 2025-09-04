# Build the project
build:
    uv sync --all-packages

# Run tests
test: build
    uv run --frozen pytest

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
