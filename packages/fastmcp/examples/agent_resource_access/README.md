# Agent Resource Access

A FastMCP server whose tools reach a third-party resource with a token from
the local Keycard CLI, rather than by exchanging the MCP caller's token as
[delegated_access](../delegated_access/) does. Use this shape for a server that
runs on a developer's machine beside the CLI: it holds no client secret, and a
human approves each new resource the first time it is requested.

## Tools

- `describe_resource_token`: obtains a token for `RESOURCE` and reports its
  expiry and, for a JWT, its unverified claims. The token itself is never
  returned, because tool output reaches the model.
- `call_resource(path)`: sends a GET to `RESOURCE_URL` + `path` with the token
  attached. The URL is configuration, not a tool argument, so the model cannot
  direct the credential elsewhere.

A refusal comes back as `{"error": <code>, "message": ...}`, where the code is
the CLI's: `permission_denied` (declined), `unauthenticated` (sign in or
authorize the resource), `failed_precondition` (no prompt on this host),
`not_found` (unknown `KEYCARD_APP_ID`).

## Usage

Under `keycard run`, which sets `KEYCARD_SOCKET` for the server it starts:

```bash
export RESOURCE=https://api.example.com RESOURCE_URL=https://api.example.com
keycard run -- uv run python main.py
```

Against the `keycard agent` daemon, which serves every signed-in zone:

```bash
export KEYCARD_SOCKET=/path/to/agent.sock
export KEYCARD_ORG=<org id> KEYCARD_ZONE=<zone id>
export RESOURCE=https://api.example.com RESOURCE_URL=https://api.example.com
uv run python main.py
```

The server speaks stdio by default; set `MCP_TRANSPORT=streamable-http` to
serve HTTP instead.

Optional:

- `SCOPES`: comma-separated scopes to request
- `KEYCARD_APP_ID`: public identifier of a Keycard application in the zone;
  the approval prompt shows its resolved name, marked self-asserted
- `KEYCARD_WORKLOAD`: the name shown in the approval prompt

## Requirements

- Python 3.10+
- A Keycard CLI new enough to serve `IssueToken`
- A host that can show the approval prompt
