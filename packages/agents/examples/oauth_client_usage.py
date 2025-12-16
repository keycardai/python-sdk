"""
Example: Using A2AServiceClientWithOAuth with PKCE user authentication.

This example demonstrates how the enhanced A2A client automatically handles
OAuth PKCE authentication (browser-based user login) when calling protected agent services.
"""

import asyncio

from keycardai.agents import A2AServiceClientWithOAuth, AgentServiceConfig


async def main():
    """Demonstrate automatic OAuth PKCE handling with A2A client."""
    
    # Configure your service (the caller)
    my_service_config = AgentServiceConfig(
        service_name="My Agent Service",
        client_id="my_service_client_id",        # From Keycard dashboard (OAuth Public Client)
        client_secret="",  # Not needed for PKCE public clients
        identity_url="https://my-service.example.com",
        zone_id="abc1234",  # Your Keycard zone ID
    )
    
    # Create OAuth-enabled A2A client
    # NOTE: Make sure to register your redirect_uri with the OAuth authorization server!
    # The redirect_uri must be registered for your client_id in Keycard/OAuth configuration
    async with A2AServiceClientWithOAuth(
        my_service_config,
        redirect_uri="http://localhost:8765/callback",  # Must be registered!
        callback_port=8765,
        # scopes=["openid", "profile"],  # Optional: only add if your auth server requires specific scopes
    ) as client:
        
        # Example 1: Call a protected service
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
            result = await client.invoke_service(
                service_url="https://protected-service.example.com",
                task={
                    "task": "Analyze this data",
                    "data": "Sample data to analyze"
                }
            )
            print(f"✅ Success: {result['result']}")
            print(f"   Delegation chain: {result['delegation_chain']}")
        except Exception as e:
            print(f"❌ Error: {e}")
        
        # Example 2: Call with user context (token exchange)
        # If you have a user's token, you can preserve the user context
        # in the delegation chain
        
        print("\nExample 2: With user context...")
        user_token = "user_access_token_from_auth_flow"
        
        try:
            result = await client.invoke_service(
                service_url="https://protected-service.example.com",
                task="Process user-specific data",
                subject_token=user_token,  # Token exchange preserves user context
            )
            print(f"✅ Success: {result['result']}")
        except Exception as e:
            print(f"❌ Error: {e}")
        
        # Example 3: Discover service capabilities first
        print("\nExample 3: Service discovery...")
        try:
            agent_card = await client.discover_service(
                "https://protected-service.example.com"
            )
            print(f"✅ Discovered service: {agent_card['name']}")
            print(f"   Capabilities: {agent_card.get('capabilities', [])}")
            print(f"   Endpoints: {list(agent_card['endpoints'].keys())}")
        except Exception as e:
            print(f"❌ Error: {e}")
        
        # Example 4: Token caching
        # After first successful OAuth, token is cached
        print("\nExample 4: Token reuse (cached)...")
        try:
            result = await client.invoke_service(
                service_url="https://protected-service.example.com",
                task="Another task",
            )
            # This call uses the cached token - no OAuth discovery needed!
            print(f"✅ Success with cached token: {result['result']}")
        except Exception as e:
            print(f"❌ Error: {e}")
        
        # Example 5: Disable automatic OAuth (manual control)
        print("\nExample 5: Manual token management...")
        try:
            # Get token explicitly
            token = await client.get_token_with_oauth_discovery(
                service_url="https://protected-service.example.com",
                www_authenticate_header=(
                    'Bearer error="invalid_token", '
                    'resource_metadata="https://protected-service.example.com/.well-known/oauth-protected-resource"'
                ),
            )
            print(f"✅ Obtained token: {token[:20]}...")
            
            # Use token explicitly
            result = await client.invoke_service(
                service_url="https://protected-service.example.com",
                task="Manual token task",
                token=token,
                auto_authenticate=False,  # Disable automatic OAuth
            )
            print(f"✅ Success with manual token: {result['result']}")
        except Exception as e:
            print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())

