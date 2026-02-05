"""
Example: Using AgentClient with PKCE user authentication.

This example demonstrates how AgentClient automatically handles
OAuth PKCE authentication (browser-based user login) when calling protected agent services.
"""

import asyncio

from keycardai.agents import AgentServiceConfig
from keycardai.agents.client import AgentClient


async def main():
    """Demonstrate automatic OAuth PKCE handling with AgentClient."""

    # Configure client identity
    # Note: For simple client usage, you may not need full service config
    my_config = AgentServiceConfig(
        service_name="My Client App",
        client_id="my_client_app_id",        # From Keycard dashboard (OAuth Public Client)
        client_secret="",  # Not needed for PKCE public clients
        identity_url="https://my-app.example.com",
        zone_id="abc1234",  # Your Keycard zone ID
        agent_executor=None,  # Not running a service, just calling others
    )

    # Create OAuth-enabled client
    # NOTE: Make sure to register your redirect_uri with OAuth authorization server!
    async with AgentClient(
        my_config,
        redirect_uri="http://localhost:8765/callback",  # Must be registered!
        callback_port=8765,
    ) as client:

        # Example 1: Call protected service
        # The client automatically:
        # 1. Attempts the call
        # 2. Receives 401 with WWW-Authenticate header
        # 3. Discovers OAuth configuration from resource_metadata URL
        # 4. Generates PKCE parameters
        # 5. Opens browser for user to log in
        # 6. Receives authorization code from callback
        # 7. Exchanges code for user's access token
        # 8. Retries the call with user token

        print("Example 1: Calling protected service with user authentication...")
        print("ℹ️  Your browser will open for login")
        try:
            result = await client.invoke(
                service_url="https://protected-service.example.com",
                task="Analyze this data",
                inputs={"data": "Sample data to analyze"}
            )
            print(f"✅ Success: {result['result']}")
            print(f"   Delegation chain: {result['delegation_chain']}")
        except Exception as e:
            print(f"❌ Error: {e}")

        # Example 2: Token caching
        # After first successful OAuth, token is cached
        print("\nExample 2: Token reuse (cached)...")
        try:
            result = await client.invoke(
                service_url="https://protected-service.example.com",
                task="Another task",
            )
            # This call uses the cached token - no OAuth discovery needed!
            print(f"✅ Success with cached token: {result['result']}")
        except Exception as e:
            print(f"❌ Error: {e}")

        # Example 3: Discover service capabilities first
        print("\nExample 3: Service discovery...")
        try:
            agent_card = await client.discover_service(
                "https://protected-service.example.com"
            )
            print(f"✅ Discovered service: {agent_card['name']}")
            print(f"   Skills: {[s['id'] for s in agent_card.get('skills', [])]}")
            print(f"   Capabilities: {agent_card.get('capabilities', {})}")
        except Exception as e:
            print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
