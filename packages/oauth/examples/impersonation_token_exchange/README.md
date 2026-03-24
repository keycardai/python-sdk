# Impersonation Token Exchange Example

Demonstrates user impersonation via OAuth 2.0 token exchange (RFC 8693) using the Keycard OAuth SDK. Users grant access through a landing page by authenticating with their identity provider and approving resource access. A background agent then obtains resource tokens on their behalf, without the user being present.

## How It Works

### Landing page (one-time, interactive)

The landing page is a web application where users sign in and grant the background agent permission to act on their behalf.

1. Serves a local web page
2. The user clicks "Continue with Keycard" and authenticates with their identity provider
3. The user grants access to the requested resources
4. Keycard creates a delegated grant for each resource

The same landing page can be used again when new resources need to be authorized.

### Background agent (repeatable, offline)

The background agent is a confidential client (e.g. `client_secret_basic` or workload identity).

1. The agent authenticates and requests a token for a specific user and resource via `client.impersonate()`
2. Keycard validates the delegated grant and issues a scoped, short-lived resource token

No browser, no user interaction. Impersonation is forbidden by default. An administrator must explicitly allow specific applications to impersonate.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) installed
- Access to Keycard Console and a Keycard zone

## Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Set up the following in Keycard Console:

1. **Set `ZONE_URL`** in `.env` to your Keycard zone URL from the Zone Settings.
2. **Create a provider** (e.g. `https://github.com` as the identity provider).
3. **Create a resource** (e.g. `https://api.github.com`) and link it to the provider.
4. **Create a Landing Page application**
   - Public credential, identifier: `landing-page`
   - Redirect URI: `http://localhost/callback`
   - Add the resource as a dependency
5. **Create a Background Agent application**
   - Password credential, identifier: `background-agent`
   - Set `AGENT_CLIENT_SECRET` in `.env`.
6. **Add a policy enabling impersonation** in Keycard Console. To allow the Background Agent app to impersonate a specific user:
   ```
   permit (
     principal is Keycard::Application,
     action,
     resource
   )
   when {
     principal.identifier == "background-agent" &&
     context.impersonate == true &&
     context has subject &&
     context.subject.identifier == "user@example.com"
   };
   ```


## Usage

### Step 1: Install dependencies

```bash
uv sync
```

### Step 2: Landing page (interactive, one-time)

Starts the landing page where users sign in and grant the background agent access to resources. In production, this would be deployed as a hosted web application. Resources are determined by the application's dependencies configured in Keycard Console.

```bash
uv run python landing_page.py --port 3000
```

Example output:

```
═══ Landing Page ═══
  Auth:       PKCE (no secret)
  Listening:  http://localhost:3000

Landing page running at http://localhost:3000
Press Ctrl+C to stop.
```

Open `http://localhost:3000` in a browser and click "Continue with Keycard".

### Step 3: Get the user identifier from Keycard Console

After the user signs in, find their identifier in Keycard Console under the Users section. The Provider can be configured to map claims (e.g. `email`, `sub`) to the user identifier on creation. The identifier can also be changed from Keycard Console at any time.

### Step 4: Run background agent (offline, repeatable)

The background agent obtains a resource token for the user without any browser interaction.

```bash
uv run python background_agent.py \
  --user-identifier user@example.com \
  --resource https://api.github.com
```

Example output:

```
═══ Background Agent ═══
  Auth:            client_credentials
  On behalf of:    user@example.com
  Access resource: https://api.github.com

Access Token: eyJhbG...
Token Type: Bearer
Expires In: 3600s
```

If the policy does not allow impersonation, you will see an error:

```
Error: OAuth error: access_denied - Access denied by policy. Policy set: <policy-set-id>. Policy set version: <policy-set-version>. Determining policies: default-user-grants.
```

### Step 5: Verify in audit logs

In Keycard Console, navigate to Audit Logs to see the user authorization and the credential issued to the background agent on behalf of the user.
