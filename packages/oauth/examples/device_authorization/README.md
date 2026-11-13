# Device Authorization Grant Example

Runs the OAuth 2.0 device authorization grant (RFC 8628) from the device's
side. The example asks the zone for a user code, prints the verification URL
and the code, and polls until a person approves or denies the request in a
browser on another device.

## Usage

```bash
uv sync
KEYCARD_ZONE_URL=https://your-zone.keycard.cloud \
KEYCARD_CLIENT_ID=your-client-id \
uv run python main.py
```

The OAuth client needs the device code grant enabled. Set
`KEYCARD_CLIENT_SECRET` for a confidential client. Without it, the example
runs as a public client. `KEYCARD_SCOPES` and `KEYCARD_RESOURCES` take
space-separated lists. The docstring at the top of `main.py` lists what to
configure in Keycard.

After a network error, a 429, or a 5xx, the example resumes polling at the
interval from `DevicePollError`, at most three times. With more than one
resource, it refreshes the token for the first resource.

## Requirements

- Python 3.10+
- keycardai-oauth package
- A Keycard zone with an OAuth client that has the device code grant enabled
