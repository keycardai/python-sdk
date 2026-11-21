# Example profile

A Hermes profile wired to one Keycard zone. Copy `config.yaml` into the
profile's Hermes home (`~/.hermes/profiles/<name>/config.yaml`, or `~/.hermes`
for the default profile), replace the placeholders, and put the application
secret in that profile's `.env`:

```
KEYCARD_CLIENT_SECRET=REPLACE_ME
```

Then, with Hermes's virtualenv active:

```bash
pip install keycardai-hermes
hermes -p <name> keycard login --actor alice
hermes -p <name> keycard token https://calendar.example.com
```

What the settings do:

- `tool_resources.calendar_search` grants the calendar resource to that tool
  only; a tool it does not name acquires nothing.
- `actor_aliases.athena` lets an A2A peer called `athena` act as `alice`.
- `actors.alice` is the zone user `alice` stands for, used by `impersonate`
  and by `verify_sign_in`.
- `secrets.SEARCH_API_KEY` is served by the plugin's secret source as an
  as-itself token for the search resource when Hermes starts.
- `mcp_servers.calendar` runs the relay; the `env` block passes the
  application secret through, since Hermes strips a stdio MCP server's
  environment.
