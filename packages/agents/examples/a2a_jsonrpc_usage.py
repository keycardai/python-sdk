"""
Example: Using A2A JSONRPC protocol with Keycard agent services.

This example demonstrates how to call agent services using the standard
A2A JSONRPC protocol instead of the custom /invoke endpoint.

Both approaches work with the same agent service - you can choose based on your needs:
- A2A JSONRPC: Standards-compliant, event-driven, supports streaming
- Custom /invoke: Simple, direct, synchronous

This example shows the A2A JSONRPC approach.
"""

import httpx

from keycardai.agents import AgentServiceConfig
from keycardai.agents.client import AgentClient


async def example_a2a_jsonrpc_call():
    """Demonstrate calling an agent service via A2A JSONRPC protocol."""

    # Configure client identity
    my_config = AgentServiceConfig(
        service_name="My Client App",
        client_id="my_client_app_id",
        client_secret="",  # Public client for PKCE
        identity_url="https://my-app.example.com",
        zone_id="abc1234",
        agent_executor=None,  # Not running a service, just calling others
    )

    # Example 1: Using A2A SDK client directly
    print("Example 1: Using A2A SDK client for JSONRPC")
    print("=" * 60)

    from a2a.client import A2AClient
    from a2a.types import Message, MessageSendParams

    # Create A2A client
    async with A2AClient(base_url="https://agent-service.example.com/a2a") as a2a_client:
        # Call agent using JSONRPC message/send method
        message = Message(
            role="user",
            parts=[{"text": "Analyze this pull request: #123"}],
        )

        params = MessageSendParams(message=message)

        try:
            # This calls POST /a2a/jsonrpc with method="message/send"
            result = await a2a_client.send_message(params)

            print(f"Task ID: {result.id}")
            print(f"Status: {result.status.state}")
            print(f"Result: {result.history[-1].parts[0]['text']}")
        except Exception as e:
            print(f"Error: {e}")

    # Example 2: Manual JSONRPC call with httpx
    print("\nExample 2: Manual JSONRPC call with httpx")
    print("=" * 60)

    async with httpx.AsyncClient() as client:
        # Construct JSONRPC request
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
            # Call A2A JSONRPC endpoint
            response = await client.post(
                "https://agent-service.example.com/a2a/jsonrpc",
                json=jsonrpc_request,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer <your-token>",
                },
            )

            result = response.json()
            print(f"JSONRPC Response: {result}")

            # Extract task from JSONRPC result
            if "result" in result:
                task = result["result"]
                print(f"Task ID: {task['id']}")
                print(f"Status: {task['status']['state']}")
        except Exception as e:
            print(f"Error: {e}")

    # Example 3: Compare with custom /invoke endpoint
    print("\nExample 3: Custom /invoke endpoint (for comparison)")
    print("=" * 60)

    async with AgentClient(
        my_config,
        redirect_uri="http://localhost:8765/callback",
        callback_port=8765,
    ) as keycard_client:
        try:
            # This calls POST /invoke (custom Keycard endpoint)
            result = await keycard_client.invoke(
                service_url="https://agent-service.example.com",
                task="What is the status of deployment?",
            )

            print(f"Result: {result['result']}")
            print(f"Delegation chain: {result['delegation_chain']}")
        except Exception as e:
            print(f"Error: {e}")


async def example_a2a_streaming():
    """Demonstrate A2A streaming with message/stream method."""

    print("\nExample 4: A2A Streaming with message/stream")
    print("=" * 60)

    from a2a.client import A2AClient
    from a2a.types import Message, MessageSendParams

    async with A2AClient(base_url="https://agent-service.example.com/a2a") as client:
        message = Message(
            role="user",
            parts=[{"text": "Generate a detailed analysis report"}],
        )

        params = MessageSendParams(message=message)

        try:
            # Stream events from agent
            async for event in client.stream_message(params):
                # Events can be Task updates, Message chunks, etc.
                print(f"Event: {event}")

                # Check if task is complete
                if hasattr(event, "status") and event.status.state == "completed":
                    print("Task completed!")
                    break
        except Exception as e:
            print(f"Error: {e}")


async def example_a2a_task_management():
    """Demonstrate A2A task management (get, cancel)."""

    print("\nExample 5: A2A Task Management")
    print("=" * 60)

    from a2a.client import A2AClient
    from a2a.types import TaskIdParams

    async with A2AClient(base_url="https://agent-service.example.com/a2a") as client:
        # Get task by ID
        try:
            task = await client.get_task(
                TaskIdParams(id="task-123")
            )

            print(f"Task: {task.id}")
            print(f"Status: {task.status.state}")
            print(f"History length: {len(task.history)}")
        except Exception as e:
            print(f"Error getting task: {e}")

        # Cancel task
        try:
            canceled_task = await client.cancel_task(
                TaskIdParams(id="task-123")
            )

            print(f"Task canceled: {canceled_task.status.state}")
        except Exception as e:
            print(f"Error canceling task: {e}")


async def main():
    """Run all A2A JSONRPC examples."""

    print("A2A JSONRPC Protocol Examples")
    print("=" * 60)
    print()
    print("This example demonstrates calling Keycard agent services")
    print("using the standard A2A JSONRPC protocol.")
    print()
    print("The server exposes both:")
    print("  1. POST /a2a/jsonrpc - A2A JSONRPC endpoint (standards-compliant)")
    print("  2. POST /invoke      - Custom Keycard endpoint (simple)")
    print()
    print("=" * 60)
    print()

    # Run examples
    await example_a2a_jsonrpc_call()
    await example_a2a_streaming()
    await example_a2a_task_management()

    print("\n" + "=" * 60)
    print("Examples complete!")
    print()
    print("Key Differences:")
    print("  A2A JSONRPC:")
    print("    - Standards-compliant protocol")
    print("    - Event-driven, supports streaming")
    print("    - Task management (get, cancel, resubscribe)")
    print("    - Uses Message/Task types")
    print()
    print("  Custom /invoke:")
    print("    - Simple request/response")
    print("    - Direct task execution")
    print("    - Delegation chain tracking")
    print("    - Easier for simple use cases")
    print()
    print("Choose based on your needs - both work with the same agent!")


if __name__ == "__main__":
    # Note: This is a demonstration example showing API usage
    # In real usage, you would:
    # 1. Have a running agent service with OAuth configured
    # 2. Obtain valid OAuth tokens
    # 3. Use actual service URLs

    print("Note: This is a code demonstration.")
    print("To run against a real service, update the URLs and credentials.")
    print()

    # Uncomment to run examples:
    # asyncio.run(main())
