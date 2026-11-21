# keycardai-hermes

Keycard plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent):
delegated, per-tool-call access with brokered credentials. Before each tool
runs, the plugin acquires a credential for the actor the turn acts as and hands
it to the tool as a non-throwing `AccessContext`; surfaces the tool-call
boundary does not reach (MCP servers, CLIs, scripts) get the same credentials
through a stdio MCP relay and `hermes keycard token`.

The contract is the [Hermes Agent integration spec](https://github.com/keycardai/keycard-sdk-spec/blob/main/specs/framework-integrations/hermes.md).
The package is experimental: it writes down what a reference deployment already
runs, and ships only what works without upstream Hermes changes.

## Install

Hermes Agent is not on PyPI, so the plugin installs into Hermes's own
virtualenv, where `hermes_agent.plugins` entry points are discovered:

```bash
# from the Hermes checkout, with its venv active
pip install keycardai-hermes
```

Requires Python 3.11 (Hermes's floor) and `keycardai-oauth>=0.36.0`.

The plugin registers a `tool_execution` middleware, a secret source and the
`hermes keycard` command group. None of them needs a declared Hermes
capability, so there is no consent step.

## Enable the plugin

One settings block per Hermes profile, in that profile's `config.yaml`:

```yaml
plugins:
  enabled:
    - keycardai-hermes
  entries:
    keycardai-hermes:
      settings:
        zone_url: https://your-zone.keycard.cloud
        application_credential:
          type: client_secret
          client_id: app_...
          secret_name: KEYCARD_CLIENT_SECRET   # read through Hermes's get_secret, from .env
        sign_in_resource: urn:hermes:assistant   # a resource the application owns
        owner_actor: alice                       # the actor for CLI, TUI, cron and the relay
        tool_resources:
          calendar_search: [https://calendar.example.com]
        # resources: [...]            # granted for every tool, built-ins included
        # request_scopes: ...
        # default_pattern: on_behalf_of | impersonate | as_self
        # actor_aliases: {athena: alice}
        # actors: {alice: alice@example.com}   # for impersonate and verify_sign_in
        # secrets: {CALENDAR_KEY: https://calendar.example.com}
        # block_without_sign_in: true
        # sign_in_on_admission: false        # answer a sender with no sign-in before the turn
        # authorization_url: https://...
        # verify_sign_in: false
```

Put the secret in the profile's `.env` as `KEYCARD_CLIENT_SECRET=...`; the
settings file never holds it. A `workload_identity` block
(`{type: workload_identity, client_id: ..., token_file: ...}`) reads a
platform-projected token file instead.

`plugins.isolation: host` is not supported: the plugin refuses to load there,
including inside the plugin host child (`HERMES_PLUGIN_HOST_PROCESS=1`),
because a ContextVar set in the plugin host process cannot reach a tool.

## Sign in an actor

```bash
hermes keycard login --actor alice
```

Registers a public client in the zone on first use (device code and refresh
token grants, no client authentication), runs the device flow for
`sign_in_resource`, and stores the sign-in under the actor in a 0600 file under
the profile's Hermes home. Without `--actor` the sign-in is stored under
`owner_actor`. With `verify_sign_in: true`, the zone's UserInfo must match
`actors[alice]` (its `sub`, or its `email` when the value is an email) or
nothing is stored. Each sign-in file is named by a readable prefix of the actor plus a digest of
the full id, so two actors never share one, and the directories are 0700.

In a gateway turn the actor is the sender (`HERMES_SESSION_ACTOR_ID`); CLI,
TUI, cron and kanban turns act as `owner_actor`. A tool call that needs a
sign-in and has none is answered with a tool result naming the login command,
and the tool does not run (`block_without_sign_in: false` records the error on
the context and runs the tool instead). With `sign_in_on_admission: true`, a
message from a sender with no sign-in is answered with the login command before
the turn starts, whether or not the turn would have called a tool.

## Read the credential in a tool

```python
from keycardai.hermes import get_access_context

def calendar_search(query: str) -> str:
    access = get_access_context()
    if access.has_resource_error("https://calendar.example.com"):
        return f"calendar unavailable: {access.get_resource_error('https://calendar.example.com')['message']}"
    token = access.access("https://calendar.example.com").access_token
    ...
```

Errors are data: a denied grant, an expired sign-in or a failed refresh is on
the `AccessContext`, never an exception out of the middleware. Outside a tool
call, `grant()` and `agrant()` install the same context:

```python
from keycardai.hermes import grant

with grant(tool_name="calendar_search") as access:
    ...
```

## A token for anything else

```bash
hermes keycard token https://calendar.example.com            # as owner_actor
hermes keycard token https://model.example.com --as-self     # as the application
```

Writes only the token to standard output and exits non-zero with the error on
standard error. It is the route for scripts and CLIs the terminal tool runs,
and the `key_cmd` of a model provider registered through
`providers.register_provider` (off unless configured). Note that the command
acts as `owner_actor` (or as the application) whichever sender's turn runs it,
and a token printed by the terminal tool lands in the session transcript; a
wrapper that writes it where the next command reads it keeps it out.

## MCP servers behind a gateway: the relay

`keycardai-hermes-mcp` is a stdio MCP server that relays to a streamable HTTP
endpoint, attaching a token for the endpoint (or `--resource`) acquired as
`owner_actor`. It retries once on a 401 with a new token and once on a 404 by
replaying `initialize`; anything that still fails is a JSON-RPC error on that
request naming the resource. In the profile's `config.yaml`:

```yaml
mcp_servers:
  calendar:
    command: keycardai-hermes-mcp
    args: ["https://gateway.example.com/mcp/calendar"]
    env:
      KEYCARD_CLIENT_SECRET: "${KEYCARD_CLIENT_SECRET}"
```

The relay runs outside Hermes, so it reads the application secret from the
environment variable named by `secret_name`. Hermes strips a stdio MCP server's
environment to a baseline, so the `env` block has to pass it through as above;
`HERMES_HOME` is set by Hermes to the profile's home, which is where the relay
reads the settings and the sign-in store. Like the token command, the relay
acts as `owner_actor`.

## Static secrets

`secrets` maps names to resources; the plugin's secret source answers each
name with an as-itself token for its resource once per process, when Hermes
hydrates secret sources. Anything that rotates goes through the middleware,
the relay or the token command instead.

## Testing

`keycardai.hermes.testing.mock_access_context` serves a synthetic context to a
tool without a zone:

```python
from keycardai.hermes.testing import mock_access_context

with mock_access_context(resource_tokens={"https://calendar.example.com": "tok"}):
    assert "tok" in calendar_search("standup")
```

See `TESTING.md` for the test matrix against the spec and `examples/profile`
for a complete profile.
