# Agent Token Source Example

Gets an access token for a third-party resource from the local Keycard agent,
holding no application credential, then optionally calls the resource with it.

## What it does

1. Asks the agent for a token for `RESOURCE` with `AgentTokenSource`. The
   first request for a resource and scope set opens an approval prompt; the
   agent remembers the approval and caches the token.
2. If `CALL_URL` is set, sends a GET to it through `ResourceAuth`, which
   attaches `Authorization: Bearer <token>`.

A refusal is reported by the predicate it matches: declined, authorization
required, no prompt available on this host, or an unknown application.

## Usage

The example depends on `keycardai-oauth` from this source tree (a path
dependency on `../../`), so it runs against unreleased code.

Under `keycard run`, which starts a per-session agent and sets
`KEYCARD_SOCKET` for the process:

```bash
uv sync
RESOURCE=https://api.example.com keycard run -- uv run python main.py
```

Against the `keycard agent` daemon, which serves every signed-in zone and so
needs the tenant named. With `KEYCARD_SOCKET` unset the source finds the
daemon's default socket the way the CLI does:

```bash
keycard agent start
export KEYCARD_ORG=<org id> KEYCARD_ZONE=<zone id>
RESOURCE=https://api.example.com uv run python main.py
```

Optional:

- `SCOPES`: comma-separated scopes to request
- `KEYCARD_APP_ID`: identifier of a Keycard application in the zone, as set on
  the application; the prompt shows it marked self-asserted
- `CALL_URL`: a URL on the resource to GET with the token attached

## Requirements

- Python 3.10+
- The Keycard CLI, signed in to a zone where `RESOURCE` is registered
- A host that can show the approval prompt
