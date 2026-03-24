#!/usr/bin/env python3
"""Background Agent app.

Authenticates with client credentials and obtains a resource-specific access
token on behalf of a user, without the user being present. The user must have
previously granted access through the landing page.
"""

import argparse
import os
import sys
from typing import NoReturn

from dotenv import load_dotenv

from keycardai.oauth import Client
from keycardai.oauth.exceptions import OAuthHttpError, OAuthProtocolError
from keycardai.oauth.http.auth import BasicAuth
from keycardai.oauth.types.models import ClientConfig


def run_background_agent(
    zone_url: str,
    client_id: str,
    client_secret: str,
    user_identifier: str,
    resource: str,
) -> None:
    """Impersonate a user and print the resulting resource token.

    Args:
        zone_url: Keycard zone URL for metadata discovery.
        client_id: Confidential client ID.
        client_secret: Confidential client secret.
        user_identifier: User identifier (e.g. email, oid).
        resource: Target resource URI.
    """
    try:
        with Client(
            base_url=zone_url,
            auth=BasicAuth(client_id, client_secret),
            config=ClientConfig(
                enable_metadata_discovery=True,
                auto_register_client=False,
            ),
        ) as client:
            response = client.impersonate(
                user_identifier=user_identifier,
                resource=resource,
            )

            print(f"Access Token: {response.access_token[:6]}...")
            print(f"Token Type: {response.token_type}")
            if response.expires_in:
                print(f"Expires In: {response.expires_in}s")
            if response.scope:
                print(f"Scope: {' '.join(response.scope)}")

    except OAuthProtocolError as e:
        desc = f" - {e.error_description}" if e.error_description else ""
        raise SystemExit(f"Error: OAuth error: {e.error}{desc}") from None
    except OAuthHttpError as e:
        raise SystemExit(f"Error: HTTP {e.status_code}: {e.response_body}") from None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _error_exit(message: str) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Background Agent: obtain a resource token on behalf of a user (offline)",
    )
    parser.add_argument(
        "--zone-url",
        default=os.getenv("ZONE_URL"),
        help="Keycard zone URL (env: ZONE_URL)",
    )
    parser.add_argument(
        "--client-id",
        default=os.getenv("AGENT_CLIENT_ID"),
        help="Confidential client ID (env: AGENT_CLIENT_ID)",
    )
    parser.add_argument(
        "--client-secret",
        default=os.getenv("AGENT_CLIENT_SECRET"),
        help="Confidential client secret (env: AGENT_CLIENT_SECRET)",
    )
    parser.add_argument(
        "--user-identifier",
        help="User identifier for impersonation",
    )
    parser.add_argument(
        "--resource",
        help="Resource URI to get a token for",
    )

    args = parser.parse_args()

    if not args.zone_url:
        _error_exit("--zone-url is required (or set ZONE_URL)")
    if not args.client_id:
        _error_exit("--client-id is required (or set AGENT_CLIENT_ID)")
    if not args.client_secret:
        _error_exit("--client-secret is required (or set AGENT_CLIENT_SECRET)")
    if not args.user_identifier:
        _error_exit("--user-identifier is required")
    if not args.resource:
        _error_exit("--resource is required")

    print("═══ Background Agent ═══")
    print("  Auth:            client_credentials")
    print(f"  On behalf of:    {args.user_identifier}")
    print(f"  Access resource: {args.resource}")
    print()

    run_background_agent(
        zone_url=args.zone_url,
        client_id=args.client_id,
        client_secret=args.client_secret,
        user_identifier=args.user_identifier,
        resource=args.resource,
    )


if __name__ == "__main__":
    main()
