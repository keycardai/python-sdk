"""Exception classes for Keycard OAuth server operations.

This module defines all custom exceptions used throughout the oauth.server package,
providing clear error types and documentation for different failure scenarios.

These exceptions are framework-free and protocol-agnostic — they do not depend on
MCP, Starlette, or any other framework.
"""

from __future__ import annotations

from typing import Any


class OAuthServerError(Exception):
    """Base exception for all Keycard OAuth server errors.

    This is the base class for all exceptions raised by the Keycard OAuth
    server package. It provides a common interface for error handling
    and allows catching all OAuth server-related errors with a single except clause.

    Attributes:
        message: Human-readable error message
        details: Optional dictionary with additional error context
    """

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        return self.message


class AuthProviderConfigurationError(OAuthServerError):
    """Raised when AuthProvider is misconfigured.

    This exception is raised during AuthProvider initialization when
    the provided configuration is invalid or incomplete.
    """

    def __init__(
        self,
        message: str | None = None,
        *,
        zone_url: str | None = None,
        zone_id: str | None = None,
        factory_type: str | None = None,
        jwks_error: bool = False,
        server_url: str | None = None,
        missing_server_url: bool = False,
    ):
        if message is None:
            if missing_server_url:
                message = (
                    "'server_url' must be provided to configure the server.\n\n"
                    "The server URL is required for the authorization callback and token exchange flow.\n\n"
                    "Examples:\n"
                    "  - server_url='http://localhost:8000'  # Local development\n"
                    "  - server_url='https://api.example.com'  # Production server\n\n"
                    "This URL will be used as the redirect_uri for OAuth callbacks.\n"
                )
            elif jwks_error:
                zone_info = f" for zone: {zone_url}" if zone_url else ""
                message = (
                    f"Failed to initialize JWKS (JSON Web Key Set) for private key identity{zone_info}\n\n"
                    "This usually indicates:\n"
                    "1. Invalid or inaccessible private key storage configuration\n"
                    "2. Insufficient permissions to create/access key storage directory\n"
                )
            elif factory_type:
                zone_info = f" for zone: {zone_url}" if zone_url else ""
                message = (
                    f"Custom client factory ({factory_type}) failed to create OAuth client{zone_info}\n\n"
                    "This indicates an issue with your custom ClientFactory implementation.\n\n"
                )
            else:
                message = (
                    "Either 'zone_url' or 'zone_id' must be provided to configure the Keycard zone.\n\n"
                    "Examples:\n"
                    "  - zone_id='abc1234'  # Will use https://abc1234.keycard.cloud\n"
                    "  - zone_url='https://abc1234.keycard.cloud'  # Direct zone URL\n\n"
                )

        details = {
            "provided_zone_url": str(zone_url) if zone_url else "unknown",
            "provided_zone_id": str(zone_id) if zone_id else "unknown",
            "provided_server_url": str(server_url) if server_url else "unknown",
            "factory_type": factory_type or "default",
            "solution": (
                "Provide server_url parameter"
                if missing_server_url
                else "Debug custom ClientFactory implementation"
                if factory_type
                else "Provide either zone_id or zone_url parameter"
            ),
        }

        super().__init__(message, details=details)


class OAuthClientConfigurationError(OAuthServerError):
    """Raised when OAuth client is misconfigured."""

    def __init__(
        self,
        message: str | None = None,
        *,
        zone_url: str | None = None,
        auth_type: str | None = None,
    ):
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
            "solution": "Verify zone configuration and network connectivity",
        }

        super().__init__(message, details=details)


class MetadataDiscoveryError(OAuthServerError):
    """Raised when Keycard zone metadata discovery fails."""

    def __init__(
        self,
        message: str | None = None,
        *,
        zone_url: str | None = None,
    ):
        if message is None:
            zone_info = f": {zone_url}" if zone_url else ""
            metadata_endpoint = (
                f"{zone_url}/.well-known/oauth-authorization-server"
                if zone_url
                else "unknown"
            )

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
            "metadata_endpoint": (
                f"{zone_url}/.well-known/oauth-authorization-server"
                if zone_url
                else "unknown"
            ),
            "solution": "Verify zone configuration and accessibility",
        }

        super().__init__(message, details=details)


class JWKSInitializationError(OAuthServerError):
    """Raised when JWKS initialization fails."""

    def __init__(self):
        super().__init__("Failed to initialize JWKS")


class JWKSValidationError(OAuthServerError):
    """Raised when JWKS URI validation fails."""

    def __init__(self):
        super().__init__("Keycard zone does not provide a JWKS URI")


class JWKSDiscoveryError(OAuthServerError):
    """JWKS discovery failed, typically due to invalid zone_id or unreachable endpoint."""

    def __init__(
        self,
        issuer: str | None = None,
        zone_id: str | None = None,
        *,
        cause: Exception | None = None,
    ):
        if issuer:
            message = f"Failed to discover JWKS from issuer: {issuer}"
            if zone_id:
                message += f" (zone: {zone_id})"
        else:
            message = "Failed to discover JWKS endpoints"
        super().__init__(message)


class TokenValidationError(OAuthServerError):
    """Token validation failed due to invalid token format, signature, or claims."""

    def __init__(self, message: str = "Token validation failed"):
        super().__init__(message)


class TokenExchangeError(OAuthServerError):
    """Raised when OAuth token exchange fails."""

    def __init__(self, message: str = "Token exchange failed"):
        super().__init__(message)


class UnsupportedAlgorithmError(OAuthServerError):
    """JWT algorithm is not supported by the verifier."""

    def __init__(self, algorithm: str):
        super().__init__(f"Unsupported JWT algorithm: {algorithm}")


class VerifierConfigError(OAuthServerError):
    """Token verifier configuration is invalid."""

    def __init__(self, message: str = "Token verifier configuration is invalid"):
        super().__init__(message)


class CacheError(OAuthServerError):
    """JWKS cache operation failed."""

    def __init__(self, message: str = "JWKS cache operation failed"):
        super().__init__(message)


class ResourceAccessError(OAuthServerError):
    """Raised when accessing a resource token fails."""

    def __init__(
        self,
        message: str | None = None,
        *,
        resource: str | None = None,
        error_type: str | None = None,
        available_resources: list[str] | None = None,
        error_details: dict | None = None,
    ):
        if message is None:
            resource_info = f"'{resource}'" if resource else "resource"

            if error_type == "global_error":
                error_msg = (
                    error_details.get("message", "Unknown global error")
                    if error_details
                    else "Unknown global error"
                )
                message = (
                    f"Cannot access resource {resource_info} due to global authentication error.\n\n"
                    f"Error: {error_msg}\n\n"
                    "This typically means the initial authentication failed. "
                    "Check your authentication setup and ensure you're properly logged in."
                )
            elif error_type == "resource_error":
                error_msg = (
                    error_details.get("message", "Unknown resource error")
                    if error_details
                    else "Unknown resource error"
                )
                message = (
                    f"Cannot access resource {resource_info} due to resource-specific error.\n\n"
                    f"Error: {error_msg}\n\n"
                    "This typically means:\n"
                    "1. Resource was not granted access during token exchange\n"
                    "2. Token exchange failed for this specific resource\n"
                    "3. Resource URL might be incorrect or not configured\n\n"
                    "Check your grant/protect decorator and ensure the resource URL is correct."
                )
            else:  # missing_token
                available_info = (
                    f": {available_resources}" if available_resources else ": none"
                )
                message = (
                    f"No access token available for resource {resource_info}.\n\n"
                    "This typically means:\n"
                    "1. Resource was not included in grant/protect decorator\n"
                    "2. Token exchange succeeded but token wasn't stored properly\n\n"
                    f"Available resources with tokens{available_info}\n\n"
                    "Fix by ensuring the resource is included in your grant/protect decorator:\n"
                    f"  @auth.protect('{resource or 'your-resource-url'}')  # <- Add this resource"
                )

        details = {
            "requested_resource": resource or "unknown",
            "error_type": error_type or "unknown",
            "available_resources": available_resources or [],
            "error_details": error_details or {},
            "solution": (
                "Fix authentication issues before accessing resources"
                if error_type == "global_error"
                else "Verify resource URL and grant configuration"
                if error_type == "resource_error"
                else "Add resource to grant/protect decorator"
            ),
        }

        super().__init__(message, details=details)


class MissingAccessContextError(OAuthServerError):
    """Raised when a grant/protect decorator encounters a missing AccessContext parameter."""

    def __init__(
        self,
        message: str | None = None,
        *,
        function_name: str | None = None,
        parameters: list[str] | None = None,
        runtime_context: bool = False,
    ):
        if message is None:
            func_info = f"'{function_name}'" if function_name else "function"

            if runtime_context:
                message = (
                    f"AccessContext parameter not found in {func_info} arguments.\n\n"
                    "This error occurs when:\n"
                    "1. AccessContext parameter is not properly annotated with type hint\n"
                    "2. AccessContext is not passed when calling the function\n\n"
                    "Ensure your function signature includes an AccessContext parameter."
                )
            else:
                message = (
                    f"Function {func_info} must have an AccessContext parameter to use grant/protect decorator.\n\n"
                    "The decorator requires AccessContext to store and retrieve access tokens.\n\n"
                    "Fix by adding AccessContext parameter:\n"
                    "  from keycardai.oauth.server import AccessContext\n\n"
                    "  @auth.protect('https://api.example.com')\n"
                    f"  async def {function_name or 'your_function'}(access_context: AccessContext, ...):\n"
                    "      if access_context.has_errors():\n"
                    "          return f'Error: {{access_context.get_errors()}}'\n"
                    "      token = access_context.access('https://api.example.com').access_token\n"
                    "      # ... rest of function"
                )

        details = {
            "function_name": function_name or "unknown",
            "current_parameters": parameters or [],
            "runtime_context": runtime_context,
            "solution": (
                "Ensure AccessContext parameter is properly type-hinted and passed"
                if runtime_context
                else "Add 'access_context: AccessContext' parameter to function signature"
            ),
        }

        super().__init__(message, details=details)


class AuthProviderInternalError(OAuthServerError):
    """Raised when an internal error occurs in AuthProvider that requires support assistance."""

    def __init__(
        self,
        message: str | None = None,
        *,
        zone_url: str | None = None,
        auth_type: str | None = None,
        component: str | None = None,
    ):
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
            "solution": "Contact Keycard support - this indicates an internal SDK issue",
        }

        super().__init__(message, details=details)


class AuthProviderRemoteError(OAuthServerError):
    """Raised when AuthProvider cannot connect to or validate the Keycard zone."""

    def __init__(
        self,
        message: str | None = None,
        *,
        zone_url: str | None = None,
        original_error: str | None = None,
    ):
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
            "metadata_endpoint": (
                f"{zone_url}/.well-known/oauth-authorization-server"
                if zone_url
                else "unknown"
            ),
            "solution": "Verify zone configuration or contact support if zone appears correct",
        }

        super().__init__(message, details=details)


class ClientInitializationError(OAuthServerError):
    """Raised when OAuth client initialization fails."""

    def __init__(self, message: str = "Failed to initialize OAuth client"):
        super().__init__(message)


class EKSWorkloadIdentityConfigurationError(OAuthServerError):
    """Raised when EKS Workload Identity is misconfigured at initialization."""

    def __init__(
        self,
        message: str | None = None,
        *,
        token_file_path: str | None = None,
        env_var_name: str | None = None,
        error_details: str | None = None,
    ):
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
                message += (
                    f"- Check that {env_var_name} environment variable is correctly set\n"
                )

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


class EKSWorkloadIdentityRuntimeError(OAuthServerError):
    """Raised when EKS Workload Identity token cannot be read at runtime."""

    def __init__(
        self,
        message: str | None = None,
        *,
        token_file_path: str | None = None,
        env_var_name: str | None = None,
        error_details: str | None = None,
    ):
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


class ClientSecretConfigurationError(OAuthServerError):
    """Raised when ClientSecret credential provider is misconfigured."""

    def __init__(
        self,
        message: str | None = None,
        *,
        credentials_type: str | None = None,
    ):
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


__all__ = [
    # Base exception
    "OAuthServerError",
    # Configuration errors
    "AuthProviderConfigurationError",
    "OAuthClientConfigurationError",
    "ClientSecretConfigurationError",
    "EKSWorkloadIdentityConfigurationError",
    # Runtime errors
    "EKSWorkloadIdentityRuntimeError",
    "TokenExchangeError",
    "ResourceAccessError",
    "MissingAccessContextError",
    # Discovery & validation errors
    "MetadataDiscoveryError",
    "JWKSInitializationError",
    "JWKSValidationError",
    "JWKSDiscoveryError",
    "TokenValidationError",
    "UnsupportedAlgorithmError",
    "VerifierConfigError",
    "CacheError",
    # Internal errors
    "AuthProviderInternalError",
    "AuthProviderRemoteError",
    "ClientInitializationError",
]
