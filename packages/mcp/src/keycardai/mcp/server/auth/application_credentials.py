"""Application Credential Providers for Token Exchange.

This module provides a protocol-based approach for managing different types of
application credentials used during OAuth 2.0 token exchange operations. Each credential
provider knows how to prepare the appropriate TokenExchangeRequest based on its
authentication method.

Key Features:
- Protocol-based abstraction for multiple credential types
- Support for client secrets, private key JWT, and workload identities
- Extensible design for adding new credential providers (EKS, GKE, Azure, etc.)

Credential Providers:
- ClientSecret: Uses client credentials (BasicAuth) for token exchange
- WebIdentity: Private key JWT client assertion (RFC 7523)
- EKSWorkloadIdentity: EKS workload identity with mounted tokens
"""

import os
import uuid
from typing import Protocol

from keycardai.oauth import (
    AsyncClient,
    AuthStrategy,
    BasicAuth,
    ClientConfig,
    MultiZoneBasicAuth,
    NoneAuth,
)
from keycardai.oauth.types.models import JsonWebKeySet, TokenExchangeRequest
from keycardai.oauth.types.oauth import GrantType, TokenEndpointAuthMethod

from ..exceptions import (
    ClientSecretConfigurationError,
    EKSWorkloadIdentityConfigurationError,
    EKSWorkloadIdentityRuntimeError,
)
from .private_key import (
    FilePrivateKeyStorage,
    PrivateKeyManager,
    PrivateKeyStorageProtocol,
)


async def _get_token_exchange_audience(client: AsyncClient) -> str:
    """Get the token exchange audience from server metadata.

    Args:
        client: OAuth client with server metadata

    Returns:
        Token endpoint URL to use as audience
    """
    if not client._initialized:
        await client._ensure_initialized()
    return client._discovered_endpoints.token


class ApplicationCredential(Protocol):
    """Protocol for application credential providers.

    Application credential providers are responsible for preparing token exchange
    requests with the appropriate authentication parameters based on the workload's
    credential type (none, private key JWT, cloud workload identity, etc.).

    This protocol enables the provider to support multiple authentication methods
    without tight coupling to specific implementations.
    """

    def get_http_client_auth(self) -> AuthStrategy:
        """Get HTTP client authentication strategy for token exchange requests.

        Returns the appropriate authentication strategy for the HTTP client that
        performs token exchange. ClientSecret credentials use the configured auth
        strategy (e.g., BasicAuth), while assertion-based credentials (WebIdentity,
        EKSWorkloadIdentity) use NoneAuth since authentication is handled via
        assertions in the request body.

        Returns:
            AuthStrategy to use for HTTP client authentication
        """
        ...

    def set_client_config(
        self,
        config: ClientConfig,
        auth_info: dict[str, str],
    ) -> ClientConfig:
        """Configure OAuth client settings for this identity type.

        Allows the identity provider to customize the OAuth client configuration
        with identity-specific settings (e.g., JWKS URL, authentication method).

        Args:
            config: Base client configuration to customize
            auth_info: Authentication context containing:
                      - resource_client_id: OAuth client identifier
                      - resource_server_url: Resource server URL
                      - zone_id: Zone identifier (optional)
                      Providers extract what they need from this dict

        Returns:
            Modified ClientConfig with identity-specific settings
        """
        ...

    async def prepare_token_exchange_request(
        self,
        client: AsyncClient,
        subject_token: str,
        resource: str,
        auth_info: dict[str, str] | None = None,
    ) -> TokenExchangeRequest:
        """Prepare a token exchange request with identity-specific parameters.

        Args:
            client: OAuth client for metadata lookup and token exchange
            subject_token: The token to be exchanged (typically access token)
            resource: Target resource URL for the exchanged token
            auth_info: Optional authentication context (zone_id, client_id, etc.)

        Returns:
            TokenExchangeRequest configured for this identity type
        """
        ...


class ClientSecret:
    """Client secret credential-based provider.

    This provider represents MCP servers that have been issued client credentials
    by Keycard. It uses client_secret_basic or client_secret_post authentication
    via the AuthStrategy, which is handled at the HTTP client level.

    The AuthStrategy is constructed from either a simple (client_id, client_secret) tuple
    for single-zone deployments, or a dict mapping zone IDs to credentials for multi-zone
    deployments.

    Example:
        # Single zone with tuple
        provider = ClientSecret(
            ("client_id_from_keycard", "client_secret_from_keycard")
        )

        # Multi-zone with different credentials per zone
        provider = ClientSecret({
            "zone1": ("client_id_1", "client_secret_1"),
            "zone2": ("client_id_2", "client_secret_2"),
        })
    """

    def __init__(
        self,
        credentials: tuple[str, str] | dict[str, tuple[str, str]],
    ):
        """Initialize with client secret credentials.

        Args:
            credentials: Either a (client_id, client_secret) tuple for single-zone
                        deployments, or a dict mapping zone_id to (client_id, client_secret)
                        tuples for multi-zone deployments.
                        - tuple: Constructs BasicAuth strategy
                        - dict: Constructs MultiZoneBasicAuth strategy
        """
        if isinstance(credentials, tuple):
            # Single zone: construct BasicAuth
            client_id, client_secret = credentials
            self.auth = BasicAuth(client_id=client_id, client_secret=client_secret)
        elif isinstance(credentials, dict):
            # Multi-zone: construct MultiZoneBasicAuth
            self.auth = MultiZoneBasicAuth(zone_credentials=credentials)
        else:
            raise ClientSecretConfigurationError(
                credentials_type=type(credentials).__name__
            )

    def get_http_client_auth(self) -> AuthStrategy:
        """Get HTTP client authentication strategy.

        Returns the configured auth strategy (typically BasicAuth or MultiZoneBasicAuth)
        for authenticating the HTTP client during token exchange.

        Returns:
            The configured authentication strategy
        """
        return self.auth

    def set_client_config(
        self,
        config: ClientConfig,
        auth_info: dict[str, str],
    ) -> ClientConfig:
        """No additional configuration needed for client secret credentials.

        Authentication is handled via AuthStrategy at the HTTP client level.

        Args:
            config: Base client configuration
            auth_info: Authentication context (unused for this provider)

        Returns:
            Unmodified ClientConfig
        """
        return config

    async def prepare_token_exchange_request(
        self,
        client: AsyncClient,
        subject_token: str,
        resource: str,
        auth_info: dict[str, str] | None = None,
    ) -> TokenExchangeRequest:
        """Prepare token exchange request with client secret credentials.

        The client authentication is handled via the AuthStrategy at the HTTP level,
        not in the token exchange request itself. This method prepares a standard
        token exchange request without client assertions.

        Args:
            client: OAuth client for token exchange
            subject_token: Access token to exchange
            resource: Target resource URL
            auth_info: Optional authentication context (unused for this provider)

        Returns:
            TokenExchangeRequest with basic parameters
        """
        return TokenExchangeRequest(
            subject_token=subject_token,
            resource=resource,
            subject_token_type="urn:ietf:params:oauth:token-type:access_token",
        )


class WebIdentity:
    """Private key JWT client assertion provider.

    This provider implements OAuth 2.0 private_key_jwt authentication as defined
    in RFC 7523. It uses a PrivateKeyManager to generate JWT client
    assertions for authenticating token exchange requests.

    The client assertion proves the client's identity using asymmetric cryptography,
    providing stronger security than shared secrets.

    Example:
        # Simple configuration with defaults
        provider = WebIdentity(
            mcp_server_name="My MCP Server",
            storage_dir="./mcp_keys"
        )

        # Advanced configuration
        custom_storage = FilePrivateKeyStorage("/secure/keys")
        provider = WebIdentity(
            mcp_server_name="My MCP Server",
            storage=custom_storage,
            key_id="stable-client-id",
            audience_config={"zone1": "https://zone1.example.com"}
        )
    """

    def __init__(
        self,
        mcp_server_name: str | None = None,
        storage: PrivateKeyStorageProtocol | None = None,
        storage_dir: str | None = None,
        key_id: str | None = None,
        audience_config: str | dict[str, str] | None = None,
    ):
        """Initialize private key identity provider.

        Args:
            mcp_server_name: Name of the MCP server (used for stable client ID)
            storage: Custom storage backend for private keys (optional)
            storage_dir: Directory for file-based key storage (default: ./mcp_keys)
            key_id: Explicit key ID (defaults to sanitized server name)
            audience_config: Audience configuration for JWT assertions:
                - str: Single audience for all zones
                - dict: Zone-specific audience mapping (zone_id -> audience)
                - None: Use issuer as audience
        """
        # Initialize storage
        if storage is not None:
            self._storage = storage
        else:
            self._storage = FilePrivateKeyStorage(storage_dir or "./mcp_keys")

        # Generate stable client ID from server name
        if key_id is None:
            stable_client_id = mcp_server_name or f"mcp-server-{uuid.uuid4()}"
            key_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in stable_client_id)

        # Initialize identity manager
        self.identity_manager = PrivateKeyManager(
            storage=self._storage,
            key_id=key_id,
            audience_config=audience_config,
        )

        # Bootstrap the identity (creates or loads keys)
        self.identity_manager.bootstrap_identity()

    def get_http_client_auth(self) -> AuthStrategy:
        """Get HTTP client authentication strategy.

        Returns NoneAuth since WebIdentity uses client assertions in the request body
        (private_key_jwt) rather than HTTP client authentication.

        Returns:
            NoneAuth instance for no HTTP client authentication
        """
        return NoneAuth()

    def set_client_config(
        self,
        config: ClientConfig,
        auth_info: dict[str, str],
    ) -> ClientConfig:
        """Configure OAuth client for private key JWT authentication.

        Sets up the client configuration with:
        - Client ID from resource_client_id
        - JWKS URL for public key distribution
        - private_key_jwt authentication method
        - Disables dynamic client registration (client should be pre-registered)

        Args:
            config: Base client configuration to customize
            auth_info: Authentication context, expects:
                      - resource_client_id: OAuth client identifier
                      - resource_server_url: Resource server URL for JWKS endpoint

        Returns:
            ClientConfig configured for private key JWT authentication

        Raises:
            KeyError: If required fields are not in auth_info
        """
        config.client_id = auth_info["resource_client_id"]
        config.auto_register_client = False
        config.client_jwks_url = self.identity_manager.get_client_jwks_url(
            auth_info["resource_server_url"]
        )
        config.client_token_endpoint_auth_method = TokenEndpointAuthMethod.PRIVATE_KEY_JWT
        config.client_grant_types = [GrantType.CLIENT_CREDENTIALS]
        return config

    def get_jwks(self) -> JsonWebKeySet:
        """Get JWKS for public key distribution.

        Returns:
            JsonWebKeySet containing the public keys
        """
        return self.identity_manager.get_jwks()

    async def prepare_token_exchange_request(
        self,
        client: AsyncClient,
        subject_token: str,
        resource: str,
        auth_info: dict[str, str] | None = None,
    ) -> TokenExchangeRequest:
        """Prepare token exchange request with JWT client assertion.

        Generates a JWT client assertion signed with the private key and includes
        it in the token exchange request for client authentication.

        Args:
            client: OAuth client for metadata lookup
            subject_token: Access token to exchange
            resource: Target resource URL
            auth_info: Must contain "resource_client_id" for JWT issuer/subject

        Returns:
            TokenExchangeRequest with JWT client assertion

        Raises:
            ValueError: If auth_info doesn't contain "resource_client_id"
        """
        if not auth_info or "resource_client_id" not in auth_info:
            raise ValueError("auth_info with 'resource_client_id' is required for WebIdentity")

        audience = await _get_token_exchange_audience(client)
        client_assertion = self.identity_manager.create_client_assertion(
            issuer=auth_info["resource_client_id"],
            audience=audience,
        )

        return TokenExchangeRequest(
            subject_token=subject_token,
            resource=resource,
            subject_token_type="urn:ietf:params:oauth:token-type:access_token",
            client_assertion_type=GrantType.JWT_BEARER_CLIENT_ASSERTION,
            client_assertion=client_assertion,
        )


class EKSWorkloadIdentity:
    """EKS workload identity provider using mounted tokens.

    This provider implements token exchange using EKS Pod Identity tokens that are
    mounted into the pod's filesystem. The token file location is configured either
    via initialization parameters or environment variables.

    The token is read fresh on each token exchange request, allowing for token rotation
    without requiring application restart.

    Environment Variable Discovery (when token_file_path is not provided):
        1. KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE - Custom token file path (highest priority)
        2. AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE - AWS EKS default location
        3. AWS_WEB_IDENTITY_TOKEN_FILE - AWS fallback location

    Example:
        # Default configuration (discovers from environment variables)
        provider = EKSWorkloadIdentity()

        # Explicit token file path
        provider = EKSWorkloadIdentity(
            token_file_path="/var/run/secrets/eks.amazonaws.com/serviceaccount/token"
        )

        # Custom environment variable
        provider = EKSWorkloadIdentity(
            env_var_name="MY_CUSTOM_TOKEN_FILE_ENV_VAR"
        )
    """
    default_env_var_names = ["AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE", "AWS_WEB_IDENTITY_TOKEN_FILE"]

    def __init__(
        self,
        token_file_path: str | None = None,
        env_var_name: str | None = None,
    ):
        """Initialize EKS workload identity provider.

        Args:
            token_file_path: Explicit path to the token file. If not provided,
                           reads from the environment variable specified by env_var_name.
            env_var_name: Name of the environment variable containing the token file path.

        Raises:
            EKSWorkloadIdentityConfigurationError: If token file cannot be read or is empty.
        """
        if token_file_path is not None:
            self.token_file_path = token_file_path
            self.env_var_name = env_var_name  # Store the env_var_name even when token_file_path is provided
        else:
            self.token_file_path, self.env_var_name = self._get_token_file_path(env_var_name)
            if not self.token_file_path:
                raise EKSWorkloadIdentityConfigurationError(
                    token_file_path=None,
                    env_var_name=env_var_name,
                    error_details="Could not find token file path in environment variables",
                )

        self._validate_token_file()

    def _get_token_file_path(self, env_var_name: str | None) -> tuple[str, str]:
        """Get the token file path from the environment variables.

        Returns:
            Tuple containing the token file path and the environment variable name.
        """
        env_names = self.default_env_var_names if env_var_name is None else [env_var_name, *self.default_env_var_names]
        return next((
            (os.environ.get(env_name), env_name)
            for env_name in env_names
            if os.environ.get(env_name)
        ), (None, None))

    def _validate_token_file(self) -> None:
        """Validate that the token file exists and can be read.

        Raises:
            EKSWorkloadIdentityConfigurationError: If token file is not accessible or empty.
        """
        try:
            with open(self.token_file_path) as f:
                token = f.read().strip()
                if not token:
                    raise EKSWorkloadIdentityConfigurationError(
                        token_file_path=self.token_file_path,
                        env_var_name=self.env_var_name,
                        error_details="Token file is empty",
                    )
        except FileNotFoundError as err:
            raise EKSWorkloadIdentityConfigurationError(
                token_file_path=self.token_file_path,
                env_var_name=self.env_var_name,
                error_details=f"Token file not found: {self.token_file_path}",
            ) from err
        except PermissionError as err:
            raise EKSWorkloadIdentityConfigurationError(
                token_file_path=self.token_file_path,
                env_var_name=self.env_var_name,
                error_details=f"Permission denied reading token file: {self.token_file_path}",
            ) from err
        except Exception as e:
            raise EKSWorkloadIdentityConfigurationError(
                token_file_path=self.token_file_path,
                env_var_name=self.env_var_name,
                error_details=f"Error reading token file: {str(e)}",
            ) from e

    def _read_token(self) -> str:
        """Read the token from the file system.

        The token is read fresh on each call to support token rotation.

        Returns:
            The token string with whitespace stripped.

        Raises:
            EKSWorkloadIdentityRuntimeError: If token cannot be read at runtime.
        """
        try:
            with open(self.token_file_path) as f:
                token = f.read().strip()
                if not token:
                    raise EKSWorkloadIdentityRuntimeError(
                        token_file_path=self.token_file_path,
                        env_var_name=self.env_var_name,
                        error_details="Token file is empty",
                    )
                return token
        except FileNotFoundError as err:
            raise EKSWorkloadIdentityRuntimeError(
                token_file_path=self.token_file_path,
                env_var_name=self.env_var_name,
                error_details=f"Token file not found: {self.token_file_path}",
            ) from err
        except PermissionError as err:
            raise EKSWorkloadIdentityRuntimeError(
                token_file_path=self.token_file_path,
                env_var_name=self.env_var_name,
                error_details=f"Permission denied reading token file: {self.token_file_path}",
            ) from err
        except Exception as e:
            raise EKSWorkloadIdentityRuntimeError(
                token_file_path=self.token_file_path,
                env_var_name=self.env_var_name,
                error_details=f"Error reading token file: {str(e)}",
            ) from e

    def get_http_client_auth(self) -> AuthStrategy:
        """Get HTTP client authentication strategy.

        Returns NoneAuth since EKSWorkloadIdentity uses client assertions in the request
        body (EKS token) rather than HTTP client authentication.

        Returns:
            NoneAuth instance for no HTTP client authentication
        """
        return NoneAuth()

    def set_client_config(
        self,
        config: ClientConfig,
        auth_info: dict[str, str],
    ) -> ClientConfig:
        """Configure OAuth client settings for EKS workload identity.

        No additional configuration is needed for EKS workload identity as the
        token is provided in the token exchange request itself.

        Args:
            config: Base client configuration
            auth_info: Authentication context (unused for this provider)

        Returns:
            Unmodified ClientConfig
        """
        return config

    async def prepare_token_exchange_request(
        self,
        client: AsyncClient,
        subject_token: str,
        resource: str,
        auth_info: dict[str, str] | None = None,
    ) -> TokenExchangeRequest:
        """Prepare token exchange request with EKS workload identity token.

        Reads the EKS token from the filesystem and includes it as the client_assertion
        in the token exchange request. The token is read fresh on each request to support
        token rotation.

        Args:
            client: OAuth client for token exchange
            subject_token: Access token to exchange
            resource: Target resource URL
            auth_info: Optional authentication context (unused for this provider)

        Returns:
            TokenExchangeRequest with EKS token as client assertion

        Raises:
            EKSWorkloadIdentityRuntimeError: If token cannot be read at runtime
        """
        # Read the token from the filesystem
        eks_token = self._read_token()

        return TokenExchangeRequest(
            subject_token=subject_token,
            resource=resource,
            subject_token_type="urn:ietf:params:oauth:token-type:access_token",
            client_assertion_type=GrantType.JWT_BEARER_CLIENT_ASSERTION,
            client_assertion=eks_token,
        )

