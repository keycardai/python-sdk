# Framework Integration Helpers

⚠️ **Note**: These helpers **do not belong** in the core `keycardai-oauth` package.

The helpers in this directory are **framework-specific** and would be part of separate integration packages:

## Proper Package Structure

### Core OAuth Package
```python
# keycardai-oauth - Pure OAuth 2.0 standards
pip install keycardai-oauth
from keycardai.oauth import Client, extract_bearer_token
```

### Framework Integration Packages  
```python
# keycardai-oauth-fastmcp - FastMCP integration helpers
pip install keycardai-oauth-fastmcp
from keycardai.oauth.integrations.fastmcp import TokenExchangeHelper

# keycardai-oauth-flask - Flask integration helpers  
pip install keycardai-oauth-flask
from keycardai.oauth.integrations.flask import token_required

# keycardai-oauth-django - Django integration helpers
pip install keycardai-oauth-django
from keycardai.oauth.integrations.django import TokenExchangeMixin
```

## Why Separate Packages?

1. **🎯 Focus**: Core OAuth package stays focused on OAuth 2.0 standards
2. **📦 Dependencies**: Framework helpers only pull in their specific dependencies
3. **🚀 Release Cycles**: Framework integrations can evolve independently
4. **🧩 Modularity**: Developers only install what they need

## File Contents

- `fastmcp_helpers.py` - Framework-specific helpers for FastMCP
- `HELPER_COMPARISON.md` - Comparison showing helper utility benefits (moved here)

These are examples of what would be in `keycardai-oauth-fastmcp` package.
