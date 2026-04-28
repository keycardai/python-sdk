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

from keycardai.agents import AgentServiceConfig
from keycardai.agents.client import AgentClient


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


async def example_invoke_comparison():
    """Compare with the custom /invoke endpoint using AgentClient."""

    print("\nExample 3: Custom /invoke endpoint (for comparison)")
    print("=" * 60)

    config = AgentServiceConfig(
        service_name="My Client App",
        client_id=os.getenv("KEYCARD_CLIENT_ID", "my_client_app_id"),
        client_secret="",
        identity_url=os.getenv("IDENTITY_URL", "https://my-app.example.com"),
        zone_id=os.getenv("KEYCARD_ZONE_ID", "your-zone-id"),
        agent_executor=None,
    )

    service_url = os.getenv(
        "AGENT_SERVICE_URL", "https://agent-service.example.com"
    )

    async with AgentClient(
        config,
        redirect_uri="http://localhost:8765/callback",
        callback_port=8765,
    ) as keycard_client:
        try:
            result = await keycard_client.invoke(
                service_url=service_url,
                task="What is the status of deployment?",
            )
            print(f"Result: {result['result']}")
            print(f"Delegation chain: {result['delegation_chain']}")
        except Exception as e:
            print(f"Error: {e}")


async def main():
    """Run all A2A JSONRPC examples."""

    print("A2A JSONRPC Protocol Examples")
    print("=" * 60)
    print()
    print("The server exposes both:")
    print("  1. POST /a2a/jsonrpc - A2A JSONRPC endpoint (standards-compliant)")
    print("  2. POST /invoke      - Custom Keycard endpoint (simple)")
    print()

    await example_manual_jsonrpc()
    await example_a2a_sdk_client()
    await example_invoke_comparison()

    print("\n" + "=" * 60)
    print("Key Differences:")
    print("  A2A JSONRPC: Standards-compliant, streaming, task management")
    print("  /invoke:     Simple request/response, delegation chain tracking")
    print("Choose based on your needs — both work with the same agent!")


def run():
    """Entry point."""
    print("Note: This is a code demonstration.")
    print("Update the URLs and credentials to run against a real service.")
    print()
    asyncio.run(main())


if __name__ == "__main__":
    run()
