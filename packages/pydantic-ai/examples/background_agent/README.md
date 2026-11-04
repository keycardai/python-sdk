# Background Agent (as itself)

A scheduled Pydantic AI agent with no user anywhere: a morning digest of open
pull requests. It runs **as itself** (`Access.as_self()`), so resource access is
attributed to the application alone, the GitHub credential lives in the zone,
and every fetch is an audit event. Nothing in this process holds a GitHub
credential. As-itself never pauses: a denied grant is a per-resource error the
tool reports back to the model.

## Keycard setup

1. Create a **zone** (or use an existing one).
2. Create an **application** for the agent and note its client ID and secret.
3. Create a **resource** for `https://api.github.com` with a GitHub credential
   (a vaulted token or a brokered GitHub App credential) and grant the
   application access to it.

## Run

```bash
export KEYCARD_ZONE_URL="https://your-zone.keycard.cloud"
export KEYCARD_CLIENT_ID="your-agent-client-id"
export KEYCARD_CLIENT_SECRET="your-agent-client-secret"
export DIGEST_REPOS="your-org/repo-one,your-org/repo-two"
export ANTHROPIC_API_KEY="sk-ant-..."

uv run main.py
```

Run it once before granting the application access to the resource to see the
as-itself behavior: the run completes and the model is told access is not
granted, with no pause.

## What to look at

- `deps=Access.as_self()` on the run: the client-credentials grant, no token
  exchange and no impersonation.
- The audit actor in the zone is the application alone, not agent-for-user.
