"""
Example: Using the A2A JSONRPC protocol with Keycard agent services.

Demonstrates how to call agent services using the standard A2A JSONRPC protocol.

Both approaches work with the same agent service — choose based on your needs:
- A2A JSONRPC: Standards-compliant, event-driven, supports streaming
- Custom /invoke: Simple, direct, synchronous
"""

import asyncio
import os

import httpx


async def example_manual_jsonrpc():
    """Call an agent service via manual JSONRPC request."""

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

            if "result" in result:
                task = result["result"]
                print(f"Task ID: {task['id']}")
                print(f"Status: {task['status']['state']}")
        except Exception as e:
            print(f"Error: {e}")


async def example_a2a_sdk_client():
    """Call an agent service via the A2A SDK client."""

    service_url = os.getenv(
        "AGENT_SERVICE_URL", "https://agent-service.example.com"
    )

    print("\nExample 2: Using A2A SDK client")
    print("=" * 60)

    from a2a.client import A2AClient
    from a2a.types import Message, MessageSendParams

    async with A2AClient(base_url=f"{service_url}/a2a") as a2a_client:
        message = Message(
            role="user",
            parts=[{"text": "Analyze this pull request: #123"}],
        )

        params = MessageSendParams(message=message)

        try:
            result = await a2a_client.send_message(params)
            print(f"Task ID: {result.id}")
            print(f"Status: {result.status.state}")
            print(f"Result: {result.history[-1].parts[0]['text']}")
        except Exception as e:
            print(f"Error: {e}")


async def main():
    """Run A2A JSONRPC examples."""

    print("A2A JSONRPC Protocol Examples")
    print("=" * 60)
    print()
    print("Demonstrates calling an agent service over A2A JSONRPC, both via a")
    print("manual httpx request and via the A2A SDK client. The bearer token")
    print("must already be obtained out-of-band (e.g. via")
    print("`keycardai.oauth.pkce.authenticate` for user-side flows).")
    print()

    await example_manual_jsonrpc()
    await example_a2a_sdk_client()


def run():
    """Entry point."""
    print("Note: This is a code demonstration.")
    print("Update the URLs and credentials to run against a real service.")
    print()
    asyncio.run(main())


if __name__ == "__main__":
    run()
