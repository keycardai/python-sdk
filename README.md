# Keycard Python SDK

A collection of Python packages for Keycard services, organized as a uv workspace.

## Overview

This workspace contains multiple Python packages that provide various Keycard functionality:

- **keycardai-oauth**: OAuth 2.0 implementation with support for RFC 8693 (Token Exchange), RFC 7662 (Introspection), RFC 7009 (Revocation), and more
- Additional packages will be added as the SDK grows

## Getting Started

### Prerequisites

- Python 3.10 or higher
- [uv](https://docs.astral.sh/uv/) package manager

### Installation

1. Clone the repository:
```bash
git clone https://github.com/keycardai/python-sdk.git
cd python-sdk
```

2. Install the workspace:
```bash
uv sync
```

### Development

This project uses uv workspaces to manage multiple related packages. Each package lives in the `packages/` directory and has its own `pyproject.toml`.

#### Working with the workspace

- **Install all dependencies**: `uv sync`
- **Run commands in the workspace root**: `uv run <command>`
- **Run commands in a specific package**: `uv run --package <package-name> <command>`
- **Add dependencies to the workspace**: Add to the root `pyproject.toml`
- **Add dependencies to a specific package**: Add to the package's `pyproject.toml`

#### Adding a new package

1. Create a new directory in `packages/`
2. Initialize the package: `uv init packages/your-package-name`
3. The package will automatically be included in the workspace

#### Development Tools

The workspace includes several development tools:

- **Code formatting**: `uv run black .` and `uv run isort .`
- **Linting**: `uv run ruff check .`
- **Type checking**: `uv run mypy .`
- **Testing**: `uv run pytest`

Or install development dependencies and run directly:
```bash
uv sync --extra dev
uv run black .
uv run pytest
```

## Package Structure

```
keycard-python-sdk/
├── pyproject.toml          # Workspace root configuration
├── README.md              # This file
├── packages/              # Individual packages
│   ├── keycardai-oauth/   # OAuth 2.0 implementation package
│   └── ...               # Additional packages
├── tests/                 # Shared tests (if any)
└── uv.lock               # Shared lockfile
```

## Workspace Benefits

Using a uv workspace provides several advantages:

- **Consistent Dependencies**: All packages share the same lockfile, ensuring consistent versions
- **Cross-package Development**: Easy to develop and test packages that depend on each other
- **Simplified CI/CD**: Single lockfile and unified testing across all packages
- **Shared Development Tools**: Common linting, formatting, and testing configuration

## Architecture Decision Records

Important architectural and design decisions are documented using [Architecture Decision Records (ADRs)](./docs/decisions/). These help explain the reasoning behind key technical choices in the project.

- [ADR-0001: Use uv Workspaces for Multi-Package Development](./docs/decisions/0001-use-uv-workspaces-for-package-management.md)

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run the development tools to ensure quality
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For questions, issues, or support:

- GitHub Issues: [https://github.com/keycardai/python-sdk/issues](https://github.com/keycardai/python-sdk/issues)
- Documentation: [https://docs.keycardai.com](https://docs.keycardai.com)
- Email: support@keycardai.com
