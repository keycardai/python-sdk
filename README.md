# KeyCard Python SDK

A collection of Python packages for KeyCard services, organized as a uv workspace.

## Overview

This workspace contains multiple Python packages that provide various KeyCard functionality:

- **keycardai-oauth**: OAuth 2.0 implementation with support for RFC 8693 (Token Exchange), RFC 7662 (Introspection), RFC 7009 (Revocation), and more
- **keycardai-mcp**: Core MCP (Model Context Protocol) integration utilities
- **keycardai-mcp-fastmcp**: FastMCP-specific integration package with decorators and middleware

## Getting Started

### Prerequisites

- Python 3.10 or higher
- [uv](https://docs.astral.sh/uv/) package manager
- [just](https://github.com/casey/just) task runner (optional, for convenience commands)

### Installation

1. Clone the repository:
```bash
git clone git@github.com:keycardai/python-sdk.git
cd python-sdk
```

2. Install the workspace:
```bash
uv sync
```

## Documentation

### Launch Documentation Server

The project includes comprehensive documentation built with Mint. To view the docs locally:

```bash
# Using just (recommended)
just docs

# Or directly with npx
cd docs && npx --yes mint@latest dev
```

This will start a local documentation server (typically at `http://localhost:3000`) with:
- API reference for all packages
- Usage examples  
- Integration guides
- Architecture decisions

### Generate API Documentation

To regenerate the API reference documentation:

```bash
# Generate docs for all packages
just sdk-ref-all

# Or generate for specific packages
just sdk-ref-oauth
just sdk-ref-mcp
just sdk-ref-mcp-fastmcp
```

## Development

This project uses uv workspaces to manage multiple related packages. Each package lives in the `packages/` directory and has its own `pyproject.toml`.

### Common Tasks

```bash
# Install all dependencies
uv sync

# Run tests
just test
# or: uv run pytest

# Lint and format code
just check          # Check for issues
just fix            # Fix auto-fixable issues
just fix-all        # Fix all issues (including unsafe fixes)

# Type checking
just typecheck
# or: uv run mypy .

# Build packages
just build
```

### Working with the workspace

- **Install all dependencies**: `uv sync`
- **Run commands in the workspace root**: `uv run <command>`
- **Run commands in a specific package**: `uv run --package <package-name> <command>`
- **Add dependencies to the workspace**: Add to the root `pyproject.toml`
- **Add dependencies to a specific package**: Add to the package's `pyproject.toml`

### Adding a new package

1. Create a new directory in `packages/`
2. Initialize the package: `uv init packages/your-package-name`
3. Update the package's `pyproject.toml` with appropriate metadata
4. The package will automatically be included in the workspace

## Package Structure

```
python-sdk/
├── pyproject.toml          # Workspace root configuration
├── justfile               # Task runner commands
├── README.md              # This file
├── docs/                  # Documentation
│   ├── docs.json          # Mint documentation config
│   ├── examples/          # Usage examples
│   ├── sdk/              # Auto-generated API reference
│   └── standards/        # Development standards
├── packages/              # Individual packages
│   ├── oauth/            # OAuth 2.0 implementation
│   ├── mcp/              # Core MCP utilities  
│   └── mcp-fastmcp/      # FastMCP integration
├── src/                   # Workspace-level source
└── uv.lock               # Shared lockfile
```

## Available Packages

### keycardai-oauth
OAuth 2.0 client implementation with comprehensive support for:
- Token Exchange (RFC 8693)
- Dynamic Client Registration (RFC 7591)
- Server Metadata Discovery (RFC 8414)
- Token Introspection (RFC 7662)
- Token Revocation (RFC 7009)

### keycardai-mcp
Core utilities for MCP (Model Context Protocol) integration.

### keycardai-mcp-fastmcp  
FastMCP-specific integration package providing:
- Authentication providers
- OAuth middleware
- Decorators for token exchange
- MCP server utilities

## Examples

Each package includes practical examples in their respective `examples/` directories:

- **OAuth examples**: Anonymous token exchange, server discovery, dynamic registration
- **MCP examples**: Google API integration with delegated token exchange

## Workspace Benefits

Using a uv workspace provides several advantages:

- **Consistent Dependencies**: All packages share the same lockfile, ensuring consistent versions
- **Cross-package Development**: Easy to develop and test packages that depend on each other
- **Simplified CI/CD**: Single lockfile and unified testing across all packages
- **Shared Development Tools**: Common linting, formatting, and testing configuration

## Architecture Decision Records

Important architectural and design decisions are documented using [Architecture Decision Records (ADRs)](./docs/project/decisions/). These help explain the reasoning behind key technical choices in the project.

- [ADR-0001: Use uv Workspaces for Multi-Package Development](./docs/project/decisions/0001-use-uv-workspaces-for-package-management.md)
- [ADR-0002: Modular Package Structure for Minimal Dependencies](./docs/project/decisions/0002-modular-package-structure-for-minimal-dependencies.md)

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run the development tools to ensure quality:
   ```bash
   just test      # Run tests
   just check     # Lint code
   just typecheck # Type checking
   ```
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For questions, issues, or support:

- GitHub Issues: [https://github.com/keycardai/python-sdk/issues](https://github.com/keycardai/python-sdk/issues)
- Documentation: [https://docs.keycardai.com](https://docs.keycardai.com)
- Email: support@keycardai.com