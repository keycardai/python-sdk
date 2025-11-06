"""Exception classes for Keycard MCP integration.

This module defines all custom exceptions used throughout the mcp package,
providing clear error types and documentation for different failure scenarios.
"""

from __future__ import annotations


class MCPServerError(Exception):
    """Base exception for all Keycard MCP server errors.

    This is the base class for all exceptions raised by the KeyCard MCP
    server package. It provides a common interface for error handling
    and allows catching all MCP server-related errors with a single except clause.

    Attributes:
        message: Human-readable error message
        details: Optional dictionary with additional error context
    """

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, str] | None = None,
    ):
        """Initialize MCP server error.

        Args:
            message: Human-readable error message
            details: Optional dictionary with additional error context
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        """Return string representation of the error."""
        return self.message


class AuthProviderConfigurationError(MCPServerError):
    """Raised when AuthProvider is misconfigured.

    This exception is raised during AuthProvider initialization when
    the provided configuration is invalid or incomplete.
    """

    def __init__(self, message: str | None = None, *, zone_url: str | None = None, zone_id: str | None = None,
                 factory_type: str | None = None, jwks_error: bool = False,
                 mcp_server_url: str | None = None, missing_mcp_server_url: bool = False):
        """Initialize configuration error with detailed context.

        Args:
            message: Custom error message (optional)
            zone_url: Provided zone_url value for context
            zone_id: Provided zone_id value for context
            factory_type: Type of custom client factory that failed (if applicable)
            jwks_error: True if this is a JWKS initialization error
            mcp_server_url: Provided mcp_server_url value for context
            missing_mcp_server_url: True if this is a missing mcp_server_url error
        """
        if message is None:
            if missing_mcp_server_url:
                # Missing MCP server URL case
                message = (
                    "'mcp_server_url' must be provided to configure the MCP server.\n\n"
                    "The MCP server URL is required for the authorization callback and token exchange flow.\n\n"
                    "Examples:\n"
                    "  - mcp_server_url='http://localhost:8000'  # Local development\n"
                    "  - mcp_server_url='https://mcp.example.com'  # Production server\n\n"
                    "This URL will be used as the redirect_uri for OAuth callbacks.\n"
                )
            elif jwks_error:
                # JWKS initialization failure case
                zone_info = f" for zone: {zone_url}" if zone_url else ""
                message = (
                    f"Failed to initialize JWKS (JSON Web Key Set) for private key identity{zone_info}\n\n"
                    "This usually indicates:\n"
                    "1. Invalid or inaccessible private key storage configuration\n"
                    "2. Insufficient permissions to create/access key storage directory\n"
                )
            elif factory_type:
                # Custom factory failure case
                zone_info = f" for zone: {zone_url}" if zone_url else ""
                message = (
                    f"Custom client factory ({factory_type}) failed to create OAuth client{zone_info}\n\n"
                    "This indicates an issue with your custom ClientFactory implementation.\n\n"
                )
            else:
                # Missing zone configuration case
                message = (
                    "Either 'zone_url' or 'zone_id' must be provided to configure the Keycard zone.\n\n"
                    "Examples:\n"
                    "  - zone_id='abc1234'  # Will use https://abc1234.keycard.cloud\n"
                    "  - zone_url='https://abc1234.keycard.cloud'  # Direct zone URL\n\n"
                )

        details = {
            "provided_zone_url": str(zone_url) if zone_url else "unknown",
            "provided_zone_id": str(zone_id) if zone_id else "unknown",
            "provided_mcp_server_url": str(mcp_server_url) if mcp_server_url else "unknown",
            "factory_type": factory_type or "default",
            "solution": "Provide mcp_server_url parameter" if missing_mcp_server_url
                       else "Debug custom ClientFactory implementation" if factory_type
                       else "Provide either zone_id or zone_url parameter",
        }

        super().__init__(message, details=details)


class OAuthClientConfigurationError(MCPServerError):
    """Raised when OAuth client is misconfigured."""

    def __init__(self, message: str | None = None, *, zone_url: str | None = None, auth_type: str | None = None):
        """Initialize OAuth client configuration error with context.

        Args:
            message: Custom error message (optional)
            zone_url: Zone URL that failed
            auth_type: Authentication type being used
        """
        if message is None:
            zone_info = f" for zone: {zone_url}" if zone_url else ""
            message = (
                f"Failed to create OAuth client{zone_info}\n\n"
                "This usually indicates:\n"
                "1. Invalid zone URL or zone not accessible\n"
                "Troubleshooting:\n"
                "- Check network connectivity to Keycard\n"
            )

        details = {
            "zone_url": str(zone_url) if zone_url else "unknown",
            "auth_type": auth_type or "unknown",
            "solution": "Verify zone configuration and network connectivity"
        }

        super().__init__(message, details=details)


class MetadataDiscoveryError(MCPServerError):
    """Raised when Keycard zone metadata discovery fails."""

    def __init__(self, message: str | None = None, *, zone_url: str | None = None):
        """Initialize zone discovery error with detailed context.

        Args:
            message: Custom error message (optional)
            zone_url: Zone URL that failed discovery
        """
        if message is None:
            zone_info = f": {zone_url}" if zone_url else ""
            metadata_endpoint = f"{zone_url}/.well-known/oauth-authorization-server" if zone_url else "unknown"

            message = (
                f"Failed to discover OAuth metadata from Keycard zone{zone_info}\n\n"
                "This usually indicates:\n"
                "1. Zone URL is incorrect or inaccessible\n"
                "2. Zone is not properly configured\n"
                "Troubleshooting:\n"
                f"- Verify zone URL is accessible: {metadata_endpoint}\n"
            )

        details = {
            "zone_url": str(zone_url) if zone_url else "unknown",
            "metadata_endpoint": f"{zone_url}/.well-known/oauth-authorization-server" if zone_url else "unknown",
            "solution": "Verify zone configuration and accessibility"
        }

        super().__init__(message, details=details)

class JWKSInitializationError(MCPServerError):
    """Raised when JWKS initialization fails."""

    def __init__(self):
        """Initialize JWKS initialization error."""
        super().__init__(
            "Failed to initialize JWKS",
        )


class JWKSValidationError(MCPServerError):
    """Raised when JWKS URI validation fails."""

    def __init__(self):
        """Initialize JWKS validation error."""
        super().__init__(
            "Keycard zone does not provide a JWKS URI",
        )


class JWKSDiscoveryError(MCPServerError):
    """JWKS discovery failed, typically due to invalid zone_id or unreachable endpoint."""

    def __init__(self, issuer: str | None = None, zone_id: str | None = None):
        """Initialize JWKS discovery error."""
        if issuer:
            message = f"Failed to discover JWKS from issuer: {issuer}"
            if zone_id:
                message += f" (zone: {zone_id})"
        else:
            message = "Failed to discover JWKS endpoints"
        super().__init__(
            message,
        )


class TokenValidationError(MCPServerError):
    """Token validation failed due to invalid token format, signature, or claims."""

    def __init__(self, message: str = "Token validation failed"):
        """Initialize token validation error."""
        super().__init__(
            message,
        )


class TokenExchangeError(MCPServerError):
    """Raised when OAuth token exchange fails."""

    def __init__(self, message: str = "Token exchange failed"):
        """Initialize token exchange error."""
        super().__init__(message)


class UnsupportedAlgorithmError(MCPServerError):
    """JWT algorithm is not supported by the verifier."""

    def __init__(self, algorithm: str):
        """Initialize unsupported algorithm error."""
        super().__init__(f"Unsupported JWT algorithm: {algorithm}")


class VerifierConfigError(MCPServerError):
    """Token verifier configuration is invalid."""

    def __init__(self, message: str = "Token verifier configuration is invalid"):
        """Initialize verifier config error."""
        super().__init__(message)


class CacheError(MCPServerError):
    """JWKS cache operation failed."""

    def __init__(self, message: str = "JWKS cache operation failed"):
        """Initialize cache error."""
        super().__init__(message)


class MissingContextError(MCPServerError):
    """Raised when grant decorator encounters a missing context error."""

    def __init__(self, message: str | None = None, *, function_name: str | None = None,
                 parameters: list[str] | None = None, runtime_context: bool = False):
        """Initialize missing context error with detailed guidance.

        Args:
            message: Custom error message (optional)
            function_name: Name of the function missing Context
            parameters: Current function parameters
            runtime_context: True if Context parameter exists but wasn't found at runtime
        """
        if message is None:
            func_info = f"'{function_name}'" if function_name else "function"

            if runtime_context:
                message = (
                    f"Context parameter not found in {func_info} arguments.\n\n"
                    "This error occurs when:\n"
                    "1. Context parameter is not properly annotated with type hint\n"
                    "2. Context is not passed when calling the function\n\n"
                    "Ensure your function signature looks like:\n"
                    f"  def {function_name or 'your_function'}(ctx: Context, ...):  # <- Context must be type-hinted\n\n"
                    "And Context is passed when calling the function."
                )
            else:
                message = (
                    f"Function {func_info} must have a Context parameter to use @grant decorator.\n\n"
                    "The @grant decorator requires access to Context to store access tokens.\n\n"
                    "Fix by adding Context parameter:\n"
                    "  from fastmcp import Context\n\n"
                    "  @auth_provider.grant('https://api.example.com')\n"
                    f"  def {function_name or 'your_function'}(ctx: Context, ...):  # <- Add 'ctx: Context' parameter\n"
                    "      access_context = ctx.get_state('keycardai')\n"
                    "      # ... rest of function"
                )

        details = {
            "function_name": function_name or "unknown",
            "current_parameters": parameters or [],
            "runtime_context": runtime_context,
            "solution": "Add 'ctx: Context' parameter to function signature" if not runtime_context else "Ensure Context parameter is properly type-hinted and passed",
        }

        super().__init__(message, details=details)


class MissingAccessContextError(MCPServerError):
    """Raised when grant decorator encounters a missing AccessContext error."""

    def __init__(self, message: str | None = None, *, function_name: str | None = None,
                 parameters: list[str] | None = None, runtime_context: bool = False):
        """Initialize missing access context error with detailed guidance.

        Args:
            message: Custom error message (optional)
            function_name: Name of the function missing AccessContext
            parameters: Current function parameters
            runtime_context: True if AccessContext parameter exists but wasn't found at runtime
        """
        if message is None:
            func_info = f"'{function_name}'" if function_name else "function"

            if runtime_context:
                message = (
                    f"AccessContext parameter not found in {func_info} arguments.\n\n"
                    "This error occurs when:\n"
                    "1. AccessContext parameter is not properly annotated with type hint\n"
                    "2. AccessContext is not passed when calling the function\n\n"
                    "Ensure your function signature looks like:\n"
                    f"  def {function_name or 'your_function'}(ctx: Context, access_context: AccessContext, ...):  # <- AccessContext must be type-hinted\n\n"
                    "And AccessContext is passed when calling the function."
                )
            else:
                message = (
                    f"Function {func_info} must have an AccessContext parameter to use @grant decorator.\n\n"
                    "The @grant decorator requires access to AccessContext to store and retrieve access tokens.\n\n"
                    "Fix by adding AccessContext parameter:\n"
                    "  from keycardai.mcp.integrations.fastmcp import AccessContext\n"
                    "  from fastmcp import Context\n\n"
                    "  @auth_provider.grant('https://api.example.com')\n"
                    f"  def {function_name or 'your_function'}(ctx: Context, access_context: AccessContext, ...):  # <- Add 'access_context: AccessContext' parameter\n"
                    "      if access_context.has_errors():\n"
                    "          return f'Error: {access_context.get_errors()}'\n"
                    "      token = access_context.access('https://api.example.com').access_token\n"
                    "      # ... rest of function"
                )

        details = {
            "function_name": function_name or "unknown",
            "current_parameters": parameters or [],
            "runtime_context": runtime_context,
            "solution": "Add 'access_context: AccessContext' parameter to function signature" if not runtime_context else "Ensure AccessContext parameter is properly type-hinted and passed",
        }

        super().__init__(message, details=details)


class ResourceAccessError(MCPServerError):
    """Raised when accessing a resource token fails."""

    def __init__(self, message: str | None = None, *, resource: str | None = None,
                 error_type: str | None = None, available_resources: list[str] | None = None,
                 error_details: dict | None = None):
        """Initialize resource access error with context.

        Args:
            message: Custom error message (optional)
            resource: Resource that failed to be accessed
            error_type: Type of error (global_error, resource_error, missing_token)
            available_resources: List of resources that have tokens
            error_details: Additional error details from the context
        """
        if message is None:
            resource_info = f"'{resource}'" if resource else "resource"

            if error_type == "global_error":
                error_msg = error_details.get('error', 'Unknown global error') if error_details else 'Unknown global error'
                message = (
                    f"Cannot access resource {resource_info} due to global authentication error.\n\n"
                    f"Error: {error_msg}\n\n"
                    "This typically means the initial authentication failed. "
                    "Check your authentication setup and ensure you're properly logged in."
                )
            elif error_type == "resource_error":
                error_msg = error_details.get('error', 'Unknown resource error') if error_details else 'Unknown resource error'
                message = (
                    f"Cannot access resource {resource_info} due to resource-specific error.\n\n"
                    f"Error: {error_msg}\n\n"
                    "This typically means:\n"
                    "1. Resource was not granted access during token exchange\n"
                    "2. Token exchange failed for this specific resource\n"
                    "3. Resource URL might be incorrect or not configured\n\n"
                    "Check your @grant() decorator and ensure the resource URL is correct."
                )
            else:  # missing_token
                available_info = f": {available_resources}" if available_resources else ": none"
                message = (
                    f"No access token available for resource {resource_info}.\n\n"
                    "This typically means:\n"
                    "1. Resource was not included in @grant() decorator\n"
                    "2. Token exchange succeeded but token wasn't stored properly\n\n"
                    f"Available resources with tokens{available_info}\n\n"
                    "Fix by ensuring the resource is included in your @grant() decorator:\n"
                    f"  @auth_provider.grant('{resource or 'your-resource-url'}')  # <- Add this resource"
                )

        details = {
            "requested_resource": resource or "unknown",
            "error_type": error_type or "unknown",
            "available_resources": available_resources or [],
            "error_details": error_details or {},
            "solution": "Fix authentication issues before accessing resources" if error_type == "global_error"
                       else "Verify resource URL and grant configuration" if error_type == "resource_error"
                       else "Add resource to @grant() decorator"
        }

        super().__init__(message, details=details)


class AuthProviderInternalError(MCPServerError):
    """Raised when an internal error occurs in AuthProvider that requires support assistance."""

    def __init__(self, message: str | None = None, *, zone_url: str | None = None,
                 auth_type: str | None = None, component: str | None = None):
        """Initialize internal error with context.

        Args:
            message: Custom error message (optional)
            zone_url: Zone URL being used
            auth_type: Authentication type being used
            component: Component that failed (e.g., "default_client_factory")
        """
        if message is None:
            component_info = f" in {component}" if component else ""
            zone_info = f" for zone: {zone_url}" if zone_url else ""
            message = (
                f"Internal error occurred{component_info}{zone_info}\n\n"
                "This is an unexpected internal issue that should not happen under normal circumstances.\n\n"

                "Please contact Keycard support with the following information:\n"
                f"- Zone URL: {zone_url or 'unknown'}\n"
                f"- Auth Type: {auth_type or 'unknown'}\n"
                f"- Component: {component or 'unknown'}\n"
                "- Full error details and stack trace\n\n"
                "Support: support@keycard.ai"
            )

        details = {
            "zone_url": str(zone_url) if zone_url else "unknown",
            "auth_type": auth_type or "unknown",
            "component": component or "unknown",
            "support_email": "support@keycard.ai",
            "solution": "Contact Keycard support - this indicates an internal SDK issue"
        }

        super().__init__(message, details=details)


class AuthProviderRemoteError(MCPServerError):
    """Raised when AuthProvider cannot connect to or validate the Keycard zone."""

    def __init__(self, message: str | None = None, *, zone_url: str | None = None,
                 original_error: str | None = None):
        """Initialize remote error with context.

        Args:
            message: Custom error message (optional)
            zone_url: Zone URL that failed
        """
        if message is None:
            zone_info = f": {zone_url}" if zone_url else ""

            message = (
                f"Failed to connect to Keycard zone{zone_info}\n\n"
                "This usually indicates:\n"
                "1. Incorrect zone_id or zone_url\n"
                "2. Zone is not accessible or doesn't exist\n"
                "If the zone configuration looks correct and you can access it manually,\n"
                "contact Keycard support at: support@keycard.ai"
            )

        details = {
            "zone_url": str(zone_url) if zone_url else "unknown",
            "metadata_endpoint": f"{zone_url}/.well-known/oauth-authorization-server" if zone_url else "unknown",
            "solution": "Verify zone configuration or contact support if zone appears correct"
        }

        super().__init__(message, details=details)


class ClientInitializationError(MCPServerError):
    """Raised when OAuth client initialization fails."""

    def __init__(self, message: str = "Failed to initialize OAuth client"):
        """Initialize client initialization error."""
        super().__init__(message)


class EKSWorkloadIdentityConfigurationError(MCPServerError):
    """Raised when EKS Workload Identity is misconfigured at initialization.

    This exception is raised during EKSWorkloadIdentity initialization when
    the token file is not accessible or the configuration is invalid. This indicates
    a configuration problem that prevents the provider from starting.
    """

    def __init__(self, message: str | None = None, *, token_file_path: str | None = None,
                 env_var_name: str | None = None, error_details: str | None = None):
        """Initialize EKS Workload Identity configuration error with detailed context.

        Args:
            message: Custom error message (optional)
            token_file_path: Path to the token file that failed
            env_var_name: Environment variable name used for token file path
            error_details: Additional error details (e.g., file not found, permission denied)
        """
        if message is None:
            file_info = f": {token_file_path}" if token_file_path else ""
            env_info = f" (from {env_var_name})" if env_var_name else ""

            message = (
                f"Failed to initialize EKS workload identity{file_info}{env_info}\n\n"
                "This usually indicates:\n"
                "1. Token file does not exist or is not accessible at initialization\n"
                "2. Insufficient permissions to read the token file\n"
                "3. Environment variable is not set or points to wrong location\n\n"
            )

            if error_details:
                message += f"Error details: {error_details}\n\n"

            message += (
                "Troubleshooting:\n"
                f"- Verify the token file exists at: {token_file_path or 'unknown'}\n"
            )

            if env_var_name:
                message += f"- Check that {env_var_name} environment variable is correctly set\n"

            message += (
                "- Ensure the process has read permissions for the token file\n"
                "- Verify EKS workload identity is properly configured for the pod\n"
            )

        details = {
            "token_file_path": str(token_file_path) if token_file_path else "unknown",
            "env_var_name": env_var_name or "unknown",
            "error_details": error_details or "unknown",
            "solution": "Verify EKS workload identity configuration and token file accessibility",
        }

        super().__init__(message, details=details)


class EKSWorkloadIdentityRuntimeError(MCPServerError):
    """Raised when EKS Workload Identity token cannot be read at runtime.

    This exception is raised during token exchange operations when the token file
    cannot be read. This indicates a runtime problem (e.g., token file was deleted,
    permissions changed, or token rotation failed) rather than a configuration issue.
    """

    def __init__(self, message: str | None = None, *, token_file_path: str | None = None,
                 env_var_name: str | None = None, error_details: str | None = None):
        """Initialize EKS Workload Identity runtime error with detailed context.

        Args:
            message: Custom error message (optional)
            token_file_path: Path to the token file that failed
            env_var_name: Environment variable name used for token file path
            error_details: Additional error details (e.g., file not found, permission denied)
        """
        if message is None:
            file_info = f": {token_file_path}" if token_file_path else ""
            env_info = f" (from {env_var_name})" if env_var_name else ""

            message = (
                f"Failed to read EKS workload identity token at runtime{file_info}{env_info}\n\n"
                "This usually indicates:\n"
                "1. Token file was deleted or moved after initialization\n"
                "2. Permissions changed on the token file\n"
                "3. Token file became empty or corrupted\n"
                "4. Token rotation failed or is incomplete\n\n"
            )

            if error_details:
                message += f"Error details: {error_details}\n\n"

            message += (
                "Troubleshooting:\n"
                f"- Verify the token file still exists at: {token_file_path or 'unknown'}\n"
                "- Check that the token file has not been deleted or moved\n"
                "- Ensure the token file is not empty\n"
                "- Verify token rotation is working correctly\n"
                "- Check file system mount status if using projected volumes\n"
            )

        details = {
            "token_file_path": str(token_file_path) if token_file_path else "unknown",
            "env_var_name": env_var_name or "unknown",
            "error_details": error_details or "unknown",
            "solution": "Verify token file is accessible and not corrupted. Check token rotation if applicable.",
        }

        super().__init__(message, details=details)


class ClientSecretConfigurationError(MCPServerError):
    """Raised when ClientSecret credential provider is misconfigured.

    This exception is raised during ClientSecret initialization when the credentials
    parameter is invalid or has an unsupported type.
    """

    def __init__(self, message: str | None = None, *, credentials_type: str | None = None):
        """Initialize ClientSecret configuration error with detailed context.

        Args:
            message: Custom error message (optional)
            credentials_type: Type of credentials that was provided
        """
        if message is None:
            type_info = f": {credentials_type}" if credentials_type else ""

            message = (
                f"Invalid credentials type provided to ClientSecret{type_info}\n\n"
                "ClientSecret requires one of the following credential formats:\n"
                "1. Tuple: (client_id, client_secret) for single-zone deployments\n"
                "2. Dict: {zone_id: (client_id, client_secret)} for multi-zone deployments\n\n"
                "Examples:\n"
                "  # Single zone\n"
                "  provider = ClientSecret(('my_client_id', 'my_client_secret'))\n\n"
                "  # Multi-zone\n"
                "  provider = ClientSecret({\n"
                "      'zone1': ('client_id_1', 'client_secret_1'),\n"
                "      'zone2': ('client_id_2', 'client_secret_2'),\n"
                "  })\n"
            )

        details = {
            "provided_type": credentials_type or "unknown",
            "expected_types": "tuple[str, str] or dict[str, tuple[str, str]]",
            "solution": "Provide credentials as either a (client_id, client_secret) tuple or a dict of zone credentials",
        }

        super().__init__(message, details=details)



# Export all exception classes
__all__ = [
    # Base exception
    "MCPServerError",

    # Specific exceptions
    "AuthProviderConfigurationError",
    "AuthProviderInternalError",
    "AuthProviderRemoteError",
    "OAuthClientConfigurationError",
    "JWKSInitializationError",
    "MetadataDiscoveryError",
    "JWKSValidationError",
    "JWKSDiscoveryError",
    "TokenValidationError",
    "TokenExchangeError",
    "UnsupportedAlgorithmError",
    "VerifierConfigError",
    "CacheError",
    "MissingContextError",
    "MissingAccessContextError",
    "ResourceAccessError",
    "ClientInitializationError",
    "ClientSecretConfigurationError",
    "EKSWorkloadIdentityConfigurationError",
    "EKSWorkloadIdentityRuntimeError",
]
