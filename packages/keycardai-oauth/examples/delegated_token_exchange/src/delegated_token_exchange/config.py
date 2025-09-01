"""Configuration management for the MCP token exchange server."""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ServerConfig:
    """Configuration for the MCP server and STS integration."""
    
    # STS Service Configuration
    sts_base_url: str
    client_id: str
    client_secret: str
    
    # JWT Token Verification
    jwks_uri: str
    issuer: str
    audience: str
    
    # Server Configuration
    host: str = "localhost"
    port: int = 8000
    resource_server_url: str = "http://localhost:8000/mcp"


def get_config() -> ServerConfig:
    """Load configuration from environment variables.
    
    Returns:
        ServerConfig with all necessary configuration values
        
    Raises:
        ValueError: If required environment variables are missing
    """
    # Required configuration
    required_vars = {
        "STS_BASE_URL": "sts_base_url",
        "CLIENT_ID": "client_id", 
        "CLIENT_SECRET": "client_secret",
        "JWKS_URI": "jwks_uri",
        "ISSUER": "issuer",
        "AUDIENCE": "audience"
    }
    
    config_values = {}
    missing_vars = []
    
    # Check for required variables
    for env_var, config_key in required_vars.items():
        value = os.getenv(env_var)
        if not value:
            missing_vars.append(env_var)
        else:
            config_values[config_key] = value
    
    if missing_vars:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_vars)}\n"
            f"Please set: {', '.join(required_vars.keys())}"
        )
    
    # Optional configuration with defaults
    config_values.update({
        "host": os.getenv("HOST", "localhost"),
        "port": int(os.getenv("PORT", "8000")),
        "resource_server_url": os.getenv(
            "RESOURCE_SERVER_URL", 
            f"http://{config_values.get('host', 'localhost')}:{os.getenv('PORT', '8000')}/mcp"
        )
    })
    
    return ServerConfig(**config_values)


# Example environment configuration
def print_example_config():
    """Print example environment configuration."""
    print("""
Example Environment Configuration:

# STS Service Configuration
export STS_BASE_URL="https://your-sts-service.com"
export CLIENT_ID="your-mcp-client-id"
export CLIENT_SECRET="your-mcp-client-secret"

# JWT Token Verification (from your identity provider)
export JWKS_URI="https://your-identity-provider.com/.well-known/jwks" 
export ISSUER="https://your-identity-provider.com"
export AUDIENCE="https://your-mcp-server.com/mcp"

# Optional Server Configuration
export HOST="localhost"  # Default: localhost
export PORT="8000"       # Default: 8000
export RESOURCE_SERVER_URL="http://localhost:8000/mcp"  # Auto-generated default

Development Example (KeyCard local setup):
export STS_BASE_URL="http://api.localdev.keycard.sh"
export CLIENT_ID="mcp-server-client"
export CLIENT_SECRET="your-client-secret"
export JWKS_URI="http://your-instance.localdev.keycard.sh/openidconnect/jwks"
export ISSUER="http://your-instance.localdev.keycard.sh"
export AUDIENCE="http://localhost:8000/mcp"
""")


if __name__ == "__main__":
    print_example_config()
