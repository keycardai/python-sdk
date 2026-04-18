"""Application Credential Providers for Token Exchange.

This module provides a protocol-based approach for managing different types of
application credentials used during OAuth 2.0 token exchange operations. Each credential
provider knows how to prepare the appropriate TokenExchangeRequest based on its
authentication method.

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

from .exceptions import (
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
    """Get the token exchange audience from server metadata."""
    if not client._initialized:
        await client._ensure_initialized()
    return client._discovered_endpoints.token


class ApplicationCredential(Protocol):
    """Protocol for application credential providers.

    Application credential providers are responsible for preparing token exchange
    requests with the appropriate authentication parameters based on the workload's
    credential type (none, private key JWT, cloud workload identity, etc.).
    """

    def get_http_client_auth(self) -> AuthStrategy:
        """Get HTTP client authentication strategy for token exchange requests."""
        ...

    def set_client_config(
        self,
        config: ClientConfig,
        auth_info: dict[str, str],
    ) -> ClientConfig:
        """Configure OAuth client settings for this identity type."""
        ...

    async def prepare_token_exchange_request(
        self,
        client: AsyncClient,
        subject_token: str,
        resource: str,
        auth_info: dict[str, str] | None = None,
    ) -> TokenExchangeRequest:
        """Prepare a token exchange request with identity-specific parameters."""
        ...


class ClientSecret:
    """Client secret credential-based provider.

    This provider represents servers that have been issued client credentials
    by Keycard. It uses client_secret_basic or client_secret_post authentication
    via the AuthStrategy.

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
        if isinstance(credentials, tuple):
            client_id, client_secret = credentials
            self.auth = BasicAuth(client_id=client_id, client_secret=client_secret)
        elif isinstance(credentials, dict):
            self.auth = MultiZoneBasicAuth(zone_credentials=credentials)
        else:
            raise ClientSecretConfigurationError(
                credentials_type=type(credentials).__name__
            )

    def get_http_client_auth(self) -> AuthStrategy:
        return self.auth

    def set_client_config(
        self,
        config: ClientConfig,
        auth_info: dict[str, str],
    ) -> ClientConfig:
        return config

    async def prepare_token_exchange_request(
        self,
        client: AsyncClient,
        subject_token: str,
        resource: str,
        auth_info: dict[str, str] | None = None,
    ) -> TokenExchangeRequest:
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

    Example:
        provider = WebIdentity(
            server_name="My Server",
            storage_dir="./server_keys"
        )
    """

    def __init__(
        self,
        server_name: str | None = None,
        storage: PrivateKeyStorageProtocol | None = None,
        storage_dir: str | None = None,
        key_id: str | None = None,
        audience_config: str | dict[str, str] | None = None,
        # Backward-compatible alias
        mcp_server_name: str | None = None,
    ):
        server_name = server_name or mcp_server_name

        if storage is not None:
            self._storage = storage
        else:
            self._storage = FilePrivateKeyStorage(storage_dir or "./server_keys")

        if key_id is None:
            stable_client_id = server_name or f"server-{uuid.uuid4()}"
            key_id = "".join(
                c if c.isalnum() or c in "-_" else "_" for c in stable_client_id
            )

        self.identity_manager = PrivateKeyManager(
            storage=self._storage,
            key_id=key_id,
            audience_config=audience_config,
        )

        self.identity_manager.bootstrap_identity()

    def get_http_client_auth(self) -> AuthStrategy:
        return NoneAuth()

    def set_client_config(
        self,
        config: ClientConfig,
        auth_info: dict[str, str],
    ) -> ClientConfig:
        config.client_id = auth_info["resource_client_id"]
        config.auto_register_client = False
        config.client_jwks_url = self.identity_manager.get_client_jwks_url(
            auth_info["resource_server_url"]
        )
        config.client_token_endpoint_auth_method = (
            TokenEndpointAuthMethod.PRIVATE_KEY_JWT
        )
        config.client_grant_types = [GrantType.CLIENT_CREDENTIALS]
        return config

    def get_jwks(self) -> JsonWebKeySet:
        return self.identity_manager.get_jwks()

    async def prepare_token_exchange_request(
        self,
        client: AsyncClient,
        subject_token: str,
        resource: str,
        auth_info: dict[str, str] | None = None,
    ) -> TokenExchangeRequest:
        if not auth_info or "resource_client_id" not in auth_info:
            raise ValueError(
                "auth_info with 'resource_client_id' is required for WebIdentity"
            )

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

    Environment Variable Discovery (when token_file_path is not provided):
        1. KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE - Custom token file path
        2. AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE - AWS EKS default location
        3. AWS_WEB_IDENTITY_TOKEN_FILE - AWS fallback location

    Example:
        # Default configuration (discovers from environment variables)
        provider = EKSWorkloadIdentity()

        # Explicit token file path
        provider = EKSWorkloadIdentity(
            token_file_path="/var/run/secrets/eks.amazonaws.com/serviceaccount/token"
        )
    """

    default_env_var_names = [
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
    ]

    def __init__(
        self,
        token_file_path: str | None = None,
        env_var_name: str | None = None,
    ):
        if token_file_path is not None:
            self.token_file_path = token_file_path
            self.env_var_name = env_var_name
        else:
            self.token_file_path, self.env_var_name = self._get_token_file_path(
                env_var_name
            )
            if not self.token_file_path:
                raise EKSWorkloadIdentityConfigurationError(
                    token_file_path=None,
                    env_var_name=env_var_name,
                    error_details="Could not find token file path in environment variables",
                )

        self._validate_token_file()

    def _get_token_file_path(
        self, env_var_name: str | None
    ) -> tuple[str, str]:
        env_names = (
            self.default_env_var_names
            if env_var_name is None
            else [env_var_name, *self.default_env_var_names]
        )
        return next(
            (
                (os.environ.get(env_name), env_name)
                for env_name in env_names
                if os.environ.get(env_name)
            ),
            (None, None),
        )

    def _validate_token_file(self) -> None:
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
        except EKSWorkloadIdentityConfigurationError:
            raise
        except Exception as e:
            raise EKSWorkloadIdentityConfigurationError(
                token_file_path=self.token_file_path,
                env_var_name=self.env_var_name,
                error_details=f"Error reading token file: {str(e)}",
            ) from e

    def _read_token(self) -> str:
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
        except EKSWorkloadIdentityRuntimeError:
            raise
        except Exception as e:
            raise EKSWorkloadIdentityRuntimeError(
                token_file_path=self.token_file_path,
                env_var_name=self.env_var_name,
                error_details=f"Error reading token file: {str(e)}",
            ) from e

    def get_http_client_auth(self) -> AuthStrategy:
        return NoneAuth()

    def set_client_config(
        self,
        config: ClientConfig,
        auth_info: dict[str, str],
    ) -> ClientConfig:
        return config

    async def prepare_token_exchange_request(
        self,
        client: AsyncClient,
        subject_token: str,
        resource: str,
        auth_info: dict[str, str] | None = None,
    ) -> TokenExchangeRequest:
        eks_token = self._read_token()

        return TokenExchangeRequest(
            subject_token=subject_token,
            resource=resource,
            subject_token_type="urn:ietf:params:oauth:token-type:access_token",
            client_assertion_type=GrantType.JWT_BEARER_CLIENT_ASSERTION,
            client_assertion=eks_token,
        )
