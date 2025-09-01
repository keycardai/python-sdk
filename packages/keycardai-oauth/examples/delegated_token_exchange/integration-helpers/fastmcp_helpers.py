"""
High-level helper utilities for common token exchange patterns.

These helpers eliminate boilerplate for popular frameworks like FastMCP,
Flask, Django, etc., making token exchange as simple as possible.
"""

from typing import Optional, List, Union
from functools import wraps

from fastmcp import Context
from starlette.requests import Request

from keycardai.oauth import Client, extract_bearer_token
from keycardai.oauth.exceptions import TokenExchangeError, AuthenticationError, OAuthError


class TokenExchangeHelper:
    """High-level helper for delegated token exchange in web frameworks."""
    
    def __init__(self, sts_client: Client):
        """Initialize with a configured STS client.
        
        Args:
            sts_client: Configured KeyCard STS client
        """
        self.sts_client = sts_client
    
    async def exchange_for_resource(
        self, 
        ctx: Context, 
        resource: str, 
        scopes: Union[str, List[str], None] = None
    ) -> str:
        """Exchange user token for resource-specific token.
        
        This is the "magic" method that handles all the boilerplate:
        - Extracts bearer token from request context
        - Handles scope parsing
        - Performs token exchange
        - Translates errors to user-friendly messages
        
        Args:
            ctx: Framework request context (FastMCP, Flask, etc.)
            resource: Target resource URL
            scopes: Scopes as string or list
            
        Returns:
            Resource-specific access token
            
        Raises:
            ValueError: If token exchange fails with user-friendly message
        """
        try:
            # Extract user token from context
            user_token = self._extract_token_from_context(ctx)
            if not user_token:
                raise ValueError("Authentication required: No valid bearer token found")
            
            # Normalize scopes
            scopes_str = self._normalize_scopes(scopes)
            
            # Perform token exchange with unified client
            response = await self.sts_client.token_exchange(
                subject_token=user_token,
                audience=resource,
                scope=scopes_str
            )
            
            return response.access_token
            
        except TokenExchangeError as e:
            raise ValueError(f"Token exchange failed: {e.error_description or e}")
        except AuthenticationError as e:
            raise ValueError(f"Authentication failed: {e}")
        except OAuthError as e:
            raise ValueError(f"Authorization service error: {e}")
        except Exception as e:
            raise ValueError(f"Unexpected error during token exchange: {e}")
    
    def _extract_token_from_context(self, ctx: Context) -> Optional[str]:
        """Extract bearer token from framework context."""
        try:
            # Handle FastMCP context
            if hasattr(ctx, 'get_http_request'):
                request = ctx.get_http_request()
                auth_header = request.headers.get("Authorization", "")
                return extract_bearer_token(auth_header)
            
            # Handle Flask context
            elif hasattr(ctx, 'headers'):
                auth_header = ctx.headers.get("Authorization", "")
                return extract_bearer_token(auth_header)
                
            # Handle Django context  
            elif hasattr(ctx, 'META'):
                auth_header = ctx.META.get("HTTP_AUTHORIZATION", "")
                return extract_bearer_token(auth_header)
                
            return None
            
        except Exception:
            return None
    
    def _normalize_scopes(self, scopes: Union[str, List[str], None]) -> Optional[str]:
        """Normalize scopes to space-separated string."""
        if not scopes:
            return None
            
        if isinstance(scopes, str):
            return scopes.strip()
            
        if isinstance(scopes, list):
            return " ".join(str(s).strip() for s in scopes if s)
            
        return None


# Convenience decorators for even cleaner code
def requires_token_exchange(resource: str, scopes: Union[str, List[str], None] = None):
    """Decorator that automatically performs token exchange for a resource.
    
    Args:
        resource: Target resource URL
        scopes: Required scopes for the resource
        
    Usage:
        @requires_token_exchange("https://api.github.com", ["repo", "user:read"])
        async def my_tool(ctx: Context, github_token: str):
            # github_token is automatically exchanged
            pass
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(ctx: Context, *args, **kwargs):
            # Get helper from context or create default
            helper = getattr(ctx, '_token_helper', None)
            if not helper:
                raise ValueError("TokenExchangeHelper not configured in context")
            
            # Exchange token
            token = await helper.exchange_for_resource(ctx, resource, scopes)
            
            # Call original function with token as first argument
            return await func(ctx, token, *args, **kwargs)
        
        return wrapper
    return decorator


# Pre-configured helpers for popular services
class ServiceHelpers:
    """Pre-configured helpers for popular API services."""
    
    def __init__(self, token_helper: TokenExchangeHelper):
        self.helper = token_helper
    
    async def google_api_token(self, ctx: Context, scopes: Optional[List[str]] = None) -> str:
        """Get Google API token with common scopes."""
        default_scopes = [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/drive"
        ]
        return await self.helper.exchange_for_resource(
            ctx, 
            "https://www.googleapis.com",
            scopes or default_scopes
        )
    
    async def github_api_token(self, ctx: Context, scopes: Optional[List[str]] = None) -> str:
        """Get GitHub API token with common scopes.""" 
        default_scopes = ["repo", "user:read"]
        return await self.helper.exchange_for_resource(
            ctx,
            "https://api.github.com", 
            scopes or default_scopes
        )
    
    async def slack_api_token(self, ctx: Context, scopes: Optional[List[str]] = None) -> str:
        """Get Slack API token with common scopes."""
        default_scopes = ["channels:read", "chat:write"]
        return await self.helper.exchange_for_resource(
            ctx,
            "https://slack.com/api",
            scopes or default_scopes
        )
    
    async def microsoft_graph_token(self, ctx: Context, scopes: Optional[List[str]] = None) -> str:
        """Get Microsoft Graph API token with common scopes."""
        default_scopes = ["https://graph.microsoft.com/Mail.Read", "https://graph.microsoft.com/User.Read"]
        return await self.helper.exchange_for_resource(
            ctx,
            "https://graph.microsoft.com",
            scopes or default_scopes
        )
