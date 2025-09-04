#!/usr/bin/env python3
"""
Dynamic Client Registration (DCR) Example with Server Metadata

Demonstrates OAuth 2.0 Dynamic Client Registration (RFC 7591) with KeyCard
using real server metadata and endpoint configuration.
Registers a client for authorization code flow with refresh token support.
"""

import os
import secrets

from keycardai.oauth import Client


def generate_client_name() -> str:
    """Generate a random client name."""
    random_id = secrets.token_hex(4)  # 8 character hex string
    return f"mcp-client-{random_id}"


def main():
    with Client(os.getenv("ZONE_URL")) as client:
        response = client.register_client(
            client_name=f"MyService-{generate_client_name()}",
        )

        print(response)



if __name__ == "__main__":
    main()
