"""
Example: Using AgentClient with PKCE user authentication.

Demonstrates how AgentClient automatically handles OAuth PKCE authentication
(browser-based user login) when calling protected agent services.
"""

import asyncio
import os

from keycardai.agents import AgentServiceConfig
from keycardai.agents.client import AgentClient


async def main():
    """Demonstrate automatic OAuth PKCE handling with AgentClient."""

    config = AgentServiceConfig(
        service_name="My Client App",
        client_id=os.getenv("KEYCARD_CLIENT_ID", "my_client_app_id"),
        client_secret="",  # Not needed for PKCE public clients
        identity_url=os.getenv("IDENTITY_URL", "https://my-app.example.com"),
        zone_id=os.getenv("KEYCARD_ZONE_ID", "your-zone-id"),
        agent_executor=None,  # Not running a service, just calling others
    )

    # Create OAuth-enabled client
    # NOTE: Make sure to register your redirect_uri with the OAuth authorization server!
    async with AgentClient(
        config,
        redirect_uri="http://localhost:8765/callback",
        callback_port=8765,
    ) as client:

        # The client automatically:
        # 1. Attempts the call
        # 2. Receives 401 with WWW-Authenticate header
        # 3. Discovers OAuth configuration from resource_metadata URL
        # 4. Generates PKCE parameters
        # 5. Opens browser for user to log in
        # 6. Receives authorization code from callback
        # 7. Exchanges code for user's access token
        # 8. Retries the call with user token

        service_url = os.getenv(
            "AGENT_SERVICE_URL", "https://protected-service.example.com"
        )

        # Call a protected service (browser opens automatically for login)
        print("Calling protected service with user authentication...")
        print("Your browser will open for login")
        try:
            result = await client.invoke(
                service_url=service_url,
                task="Analyze this data",
                inputs={"data": "Sample data to analyze"},
            )
            print(f"Success: {result['result']}")
            print(f"Delegation chain: {result['delegation_chain']}")
        except Exception as e:
            print(f"Error: {e}")

        # After first successful OAuth, the token is cached.
        # This call reuses the cached token — no browser needed.
        print("\nCalling again with cached token...")
        try:
            result = await client.invoke(
                service_url=service_url,
                task="Another task",
            )
            print(f"Success with cached token: {result['result']}")
        except Exception as e:
            print(f"Error: {e}")

        # Discover service capabilities
        print("\nDiscovering service capabilities...")
        try:
            agent_card = await client.discover_service(service_url)
            print(f"Service: {agent_card['name']}")
            print(f"Skills: {[s['id'] for s in agent_card.get('skills', [])]}")
        except Exception as e:
            print(f"Error: {e}")


def run():
    """Entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
