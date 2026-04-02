#!/usr/bin/env python3
"""Landing Page app.

Serves a local web page where the user can sign in via Keycard and establish
access grants. Handles the full PKCE authorization code flow:

  /              — Login page with "Continue with Keycard" button
  /authorize     — Generates PKCE challenge, redirects to Keycard
  /callback      — Receives authorization code, exchanges for tokens
  /error         — Displays authorization errors
"""

import argparse
import base64
import http.server
import json
import os
import secrets
import sys
import urllib.parse
from typing import NoReturn

from dotenv import load_dotenv

from keycardai.oauth import Client, build_authorize_url
from keycardai.oauth.http.auth import NoneAuth
from keycardai.oauth.types.models import ClientConfig
from keycardai.oauth.utils.pkce import PKCEGenerator

# ---------------------------------------------------------------------------
# HTML templates
# ---------------------------------------------------------------------------

def _render_login_page() -> str:
    return """\
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Sign In</title></head>
<body style="font-family:sans-serif;max-width:480px;margin:80px auto;text-align:center">
  <h1>Sign in to grant agent access</h1>
  <p>Click the button below to sign in and grant the agent access.</p>
  <a href="/authorize"
     style="display:inline-block;padding:12px 24px;background:#9A5CD0;color:#fff;
            text-decoration:none;border-radius:6px;margin-top:16px">
    Continue with Keycard
  </a>
</body></html>"""


def _render_success_page(claims: dict) -> str:
    email = claims.get("email", claims.get("sub", "unknown"))
    return f"""\
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Connected</title></head>
<body style="font-family:sans-serif;max-width:480px;margin:80px auto;text-align:center">
  <h1>&#10003; You're connected!</h1>
  <p>Signed in as <strong>{email}</strong></p>
  <p>Access granted. The background agent can now act on your behalf.</p>
</body></html>"""


def _render_error_page(error: str, description: str) -> str:
    return f"""\
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Error</title></head>
<body style="font-family:sans-serif;max-width:480px;margin:80px auto;text-align:center">
  <h1>Authorization failed</h1>
  <p style="color:#c00"><strong>{error}</strong></p>
  <p>{description}</p>
  <a href="/" style="color:#9A5CD0">Back to login</a>
</body></html>"""


# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------

def run_landing_page(
    zone_url: str,
    client_id: str,
    port: int = 8080,
    scope: str = "openid email",
) -> None:
    """Start a landing page server for the OAuth PKCE authorization flow.

    Args:
        zone_url: Keycard zone URL for metadata discovery.
        client_id: Public client ID (pre-registered in Keycard).
        port: Port to listen on.
        scope: OAuth scopes to request.
    """
    oauth_client = Client(
        base_url=zone_url,
        auth=NoneAuth(),
        config=ClientConfig(
            enable_metadata_discovery=True,
            auto_register_client=False,
        ),
    )
    authorize_endpoint = oauth_client.endpoints.authorize

    # Maps OAuth state param -> PKCE code_verifier
    pkce_store: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            if path == "/":
                self._serve_html(200, _render_login_page())
            elif path == "/authorize":
                self._handle_authorize()
            elif path == "/callback":
                self._handle_callback(qs)
            elif path == "/error":
                error = qs.get("error", ["unknown_error"])[0]
                desc = qs.get("error_description", ["An unknown error occurred."])[0]
                self._serve_html(200, _render_error_page(error, desc))
            else:
                self.send_response(404)
                self.end_headers()

        def _handle_authorize(self):
            pkce = PKCEGenerator().generate_pkce_pair()
            state = secrets.token_urlsafe(32)
            pkce_store[state] = pkce.code_verifier

            url = build_authorize_url(
                authorize_endpoint,
                client_id=client_id,
                redirect_uri=f"http://localhost:{port}/callback",
                pkce=pkce,
                resources=[],
                scope=scope,
                state=state,
            )
            self._send_redirect(url)

        def _handle_callback(self, qs: dict):
            if "error" in qs:
                error = qs["error"][0]
                desc = qs.get("error_description", [""])[0]
                redirect = f"/error?{urllib.parse.urlencode({'error': error, 'error_description': desc})}"
                self._send_redirect(redirect)
                return

            code = qs.get("code", [None])[0]
            state = qs.get("state", [None])[0]
            if not code or not state:
                self._serve_html(400, _render_error_page(
                    "missing_params", "Missing code or state parameter."))
                return

            code_verifier = pkce_store.pop(state, None)
            if not code_verifier:
                self._serve_html(400, _render_error_page(
                    "invalid_state", "State does not match any pending request."))
                return

            try:
                token_response = oauth_client.exchange_authorization_code(
                    code=code,
                    redirect_uri=f"http://localhost:{port}/callback",
                    code_verifier=code_verifier,
                    client_id=client_id,
                )
            except Exception as exc:
                error = getattr(exc, "error", "token_exchange_error")
                desc = getattr(exc, "error_description", None) or str(exc)
                self._serve_html(502, _render_error_page(error, desc))
                return

            claims: dict = {}
            if token_response.id_token:
                try:
                    payload_b64 = token_response.id_token.split(".")[1]
                    payload_b64 += "=" * (4 - len(payload_b64) % 4)
                    claims = json.loads(base64.urlsafe_b64decode(payload_b64))
                except Exception:
                    claims = {"_error": "Failed to decode ID token"}

            self._serve_html(200, _render_success_page(claims))

        def _send_redirect(self, url: str):
            # Strip CR/LF to prevent HTTP response splitting
            safe_url = url.replace("\r", "").replace("\n", "")
            self.send_response(302)
            self.send_header("Location", safe_url)
            self.end_headers()

        def _serve_html(self, status: int, html: str):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        def log_message(self, fmt, *a):
            print(f"[landing-page] {a[0]} {a[1]} {a[2]}")

    server = http.server.HTTPServer(("localhost", port), Handler)
    print(f"Landing page running at http://localhost:{port}")
    print("Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _error_exit(message: str) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Landing Page: web application where users sign in and grant the background agent permission to act on their behalf.",
    )
    parser.add_argument(
        "--zone-url",
        default=os.getenv("ZONE_URL"),
        help="Keycard zone URL (env: ZONE_URL)",
    )
    parser.add_argument(
        "--client-id",
        default=os.getenv("LANDING_PAGE_CLIENT_ID"),
        help="Public client ID for the Landing Page app (env: LANDING_PAGE_CLIENT_ID)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to serve on (default: 8080)",
    )
    parser.add_argument(
        "--scope",
        help="Additional scopes beyond 'openid email'",
    )

    args = parser.parse_args()

    if not args.zone_url:
        _error_exit("--zone-url is required (or set ZONE_URL)")
    if not args.client_id:
        _error_exit("--client-id is required (or set LANDING_PAGE_CLIENT_ID)")

    scope = "openid email"
    if args.scope:
        scope = f"openid email {args.scope}"

    print("═══ Landing Page ═══")
    print("  Auth:       PKCE (no secret)")
    print(f"  Listening:  http://localhost:{args.port}")
    print()

    run_landing_page(
        zone_url=args.zone_url,
        client_id=args.client_id,
        port=args.port,
        scope=scope,
    )


if __name__ == "__main__":
    main()
