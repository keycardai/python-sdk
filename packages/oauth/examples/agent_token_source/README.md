# Agent Token Source Example

Gets an access token for a third-party resource from the local Keycard CLI,
holding no client secret, then optionally calls the resource with it.

## What it does

1. Asks the Keycard CLI over `KEYCARD_SOCKET` for a token for `RESOURCE`.
   The first request opens an approval prompt; the CLI remembers the approval
   for the rest of its session.
2. If `CALL_URL` is set, sends a GET to it with the token attached through
   `ResourceAuth`.

A refusal is reported by the predicate it matches: declined, authorization
required, no prompt available on this host, or an unknown application.

## Usage

Under `keycard run`, which sets `KEYCARD_SOCKET` and scopes the request to the
signed-in zone:

```bash
RESOURCE=https://api.example.com keycard run -- uv run python main.py
```

Against the `keycard agent` daemon, which serves every signed-in zone and so
needs the tenant named:

```bash
export KEYCARD_SOCKET=/path/to/agent.sock
export KEYCARD_ORG=<org id> KEYCARD_ZONE=<zone id>
RESOURCE=https://api.example.com uv run python main.py
```

Optional:

- `SCOPES`: comma-separated scopes to request
- `KEYCARD_APP_ID`: public identifier of a Keycard application in the zone;
  the prompt shows its resolved name, marked self-asserted
- `CALL_URL`: a URL to GET with the token attached

## Requirements

- Python 3.10+
- A Keycard CLI new enough to serve `IssueToken` (and, for `KEYCARD_APP_ID`,
  to resolve application ids)
- A host that can show the approval prompt
