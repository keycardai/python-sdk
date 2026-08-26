# Background Agent (as itself)

A LangChain agent with no user anywhere: a scheduled PR-review digest that
fetches open pull requests from GitHub and summarizes them. It authenticates
as its own Keycard application (`Access.as_self()`), and Keycard
delivers whatever credential the zone brokers for the GitHub resource, such as
a vaulted PAT or a GitHub App token, per tool call. The worker's environment
holds no GitHub credential, and revoking access happens in one place.

## Keycard setup

1. Create a **zone** at [keycard.ai](https://keycard.ai) (or use an existing one).
2. Create an **application** for the agent and note its client ID and secret.
3. Create a **resource** for `https://api.github.com` and attach a credential
   provider that can serve it (for example a zone vault holding a GitHub
   token, or a GitHub App provider).
4. Grant the application access to the resource.

## Run

```bash
export KEYCARD_ZONE_URL="https://your-zone.keycard.cloud"
export KEYCARD_CLIENT_ID="your-agent-client-id"
export KEYCARD_CLIENT_SECRET="your-agent-client-secret"
export ANTHROPIC_API_KEY="sk-ant-..."
export DIGEST_REPOS="your-org/repo-a,your-org/repo-b"   # optional

uv run main.py
```

## What to look at

- The middleware has **no** `sign_in_url` / `authorization_url`: there is no
  user in this process, so a denied grant is an error on the `AccessContext`,
  never a consent pause.
- The tool reads its token with `get_access_context().access(...)`; no
  credential appears in the environment, the code, or the model's context.
- Each run of the digest produces audit events in the zone with the
  application as the actor.
