"""Example: calling a Keycard-protected agent service over A2A JSONRPC.

Demonstrates two paths against the standard `/a2a/jsonrpc` endpoint:
1. A manual httpx POST that builds the JSONRPC envelope by hand.
2. The a2a-sdk 1.x ``Client`` (via ``create_client``), which handles
   serialization, authentication interception, and result decoding.

Bearer tokens must be obtained out-of-band. For user-side flows use
``keycardai.oauth.pkce.authenticate`` from keycardai-oauth; for
server-to-server delegation use ``DelegationClient`` from
``keycardai.a2a.server``.
"""

import asyncio
import os

import httpx


async def example_manual_jsonrpc() -> None:
    """Call an agent service via a hand-built JSONRPC request."""

    service_url = os.getenv(
        "AGENT_SERVICE_URL", "https://agent-service.example.com"
    )

    print("Example 1: Manual JSONRPC call with httpx")
    print("=" * 60)

    async with httpx.AsyncClient() as client:
        jsonrpc_request = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"text": "What is the status of deployment?"}],
                }
            },
        }

        try:
            response = await client.post(
                f"{service_url}/a2a/jsonrpc",
                json=jsonrpc_request,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {os.getenv('AUTH_TOKEN', '<your-token>')}",
                },
            )

            result = response.json()
            print(f"JSONRPC Response: {result}")
        except Exception as e:
            print(f"Error: {e}")


async def example_a2a_sdk_client() -> None:
    """Call an agent service via the a2a-sdk 1.x ``Client``."""

    service_url = os.getenv(
        "AGENT_SERVICE_URL", "https://agent-service.example.com"
    )

    print("\nExample 2: a2a-sdk 1.x Client via create_client")
    print("=" * 60)

    from a2a.client import A2ACardResolver, ClientConfig, create_client

    auth_token = os.getenv("AUTH_TOKEN", "<your-token>")
    auth_headers = {"Authorization": f"Bearer {auth_token}"}

    async with httpx.AsyncClient(headers=auth_headers) as http_client:
        # Resolve the agent card from the service's well-known endpoint.
        resolver = A2ACardResolver(httpx_client=http_client, base_url=service_url)
        agent_card = await resolver.get_agent_card()

        # Build a Client against the resolved card.
        config = ClientConfig(httpx_client=http_client)
        client = create_client(card=agent_card, config=config)

        try:
            async for event in client.send_message(
                "What is the status of deployment?"
            ):
                print(f"Event: {event}")
        except Exception as e:
            print(f"Error: {e}")


async def main() -> None:
    print("A2A JSONRPC Protocol Examples")
    print("=" * 60)
    print()
    print("Demonstrates calling a Keycard-protected agent service over the")
    print("standard A2A JSONRPC endpoint. The bearer token must already be")
    print("obtained (e.g. via keycardai.oauth.pkce.authenticate for user")
    print("flows or DelegationClient for server-to-server delegation).")
    print()

    await example_manual_jsonrpc()
    await example_a2a_sdk_client()


def run() -> None:
    """Entry point."""
    print("Note: This is a code demonstration.")
    print("Update AGENT_SERVICE_URL and AUTH_TOKEN to run against a real service.")
    print()
    asyncio.run(main())


if __name__ == "__main__":
    run()
