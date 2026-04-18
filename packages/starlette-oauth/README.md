# keycardai-starlette-oauth

Starlette/FastAPI middleware and route builders for protecting HTTP APIs with Keycard OAuth. No MCP dependency.

## Installation

```bash
pip install keycardai-starlette-oauth
```

## Quick Start

```python
from fastapi import FastAPI, Request
from keycardai.starlette_oauth import AuthProvider
from keycardai.oauth.server import AccessContext, ClientSecret

auth = AuthProvider(
    zone_id="your-zone-id",
    application_credential=ClientSecret(("client_id", "client_secret")),
)

app = FastAPI()
auth.install(app)

@app.get("/api/data")
@auth.protect("https://api.example.com")
async def get_data(request: Request, access: AccessContext):
    token = access.access("https://api.example.com").access_token
    # Use token to call downstream API
```
