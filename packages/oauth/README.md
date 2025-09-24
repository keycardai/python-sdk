# Keycard OAuth SDK

A comprehensive Python SDK for OAuth 2.0 functionality implementing multiple OAuth 2.0 standards for enterprise-grade token management.

## Requirements

- **Python 3.9 or greater**
- Virtual environment (recommended)

## Setup Guide

### Option 1: Using uv (Recommended)

If you have [uv](https://docs.astral.sh/uv/) installed:

```bash
# Create a new project with uv
uv init my-oauth-project
cd my-oauth-project

# Create and activate virtual environment
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### Option 2: Using Standard Python

```bash
# Create project directory
mkdir my-oauth-project
cd my-oauth-project

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Upgrade pip (recommended)
pip install --upgrade pip
```

## Installation

```bash
uv add keycardai-oauth
```

## Quick Start

```python
from keycardai.oauth import Client

with Client("https://oauth.example.com/token") as client:
    response = await client.exchange_token(
        subject_token="original_token",
        subject_token_type=TokenTypes.ACCESS_TOKEN,
        resource="https://api.example.com"
    )

```

## Development

This package is part of the [Keycard Python SDK workspace](../../README.md). 

To develop:

```bash
# From workspace root
uv sync
uv run --package keycardai-oauth pytest
```

## License

MIT License - see [LICENSE](../../LICENSE) file for details.
