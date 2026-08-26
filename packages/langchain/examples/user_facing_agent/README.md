# User-Facing Agent (on behalf of)

A LangChain calendar assistant that acts **on behalf of** the person invoking
it. The caller's Keycard token is exchanged per tool call for a Google
Calendar token (RFC 8693 token exchange), so access is attributed to
agent-for-user in the audit log and revoking the user's grant cuts the agent
off immediately.

When the user has not granted calendar access yet, the middleware pauses the
run with a LangGraph `authorization_required` interrupt. This CLI prints the
consent link, waits, and resumes the same run. In a chat UI the same payload
becomes an in-chat sign-in card.

## Keycard setup

1. Create a **zone** at [keycard.ai](https://keycard.ai) (or use an existing one).
2. Create an **application** for the agent and note its client ID and secret.
3. Create a **resource** for `https://www.googleapis.com/calendar/v3` with a
   Google OAuth credential provider.
4. Sign in through the agent's application to obtain a subject token for the
   user (any OAuth client can drive this; the token must be issued by your
   zone with the agent's application as the audience owner).

## Run

```bash
export KEYCARD_ZONE_URL="https://your-zone.keycard.cloud"
export KEYCARD_CLIENT_ID="your-agent-client-id"
export KEYCARD_CLIENT_SECRET="your-agent-client-secret"
export KEYCARD_SUBJECT_TOKEN="<the caller's Keycard access token>"
export ANTHROPIC_API_KEY="sk-ant-..."

uv run main.py "what's on my calendar today?"
```

## What to look at

- The identity for the run is `Access.on_behalf_of(...)`, passed as
  LangChain runtime context, not middleware state, so one deployed agent
  serves many users.
- The interrupt/resume loop at the bottom of `main.py`: consent changes the
  grant in the zone, not the token in your session, so the resume retries the
  exchange with the same subject token and succeeds.
- The tool never sees a Google credential until the moment of the call, and
  the model never sees one at all.
