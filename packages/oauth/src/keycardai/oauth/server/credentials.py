"""Application Credential Providers for Token Exchange.

This module provides a protocol-based approach for managing different types of
application credentials used during OAuth 2.0 token exchange operations. Each credential
provider knows how to prepare the appropriate TokenExchangeRequest based on its
authentication method.

Credential Providers:
- ClientSecret: Uses client credentials (BasicAuth) for token exchange
- WebIdentity: Private key JWT client assertion (RFC 7523)
- WorkloadIdentity: Platform-signed OIDC token from a pluggable IdentityTokenSource
- EKSWorkloadIdentity: Deprecated alias for WorkloadIdentity with a FileTokenSource
"""

import inspect
import os
import uuid
import warnings
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx

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
    WorkloadIdentityConfigurationError,
    WorkloadIdentityRuntimeError,
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

        # Multi-zone with different credentials per zone, keyed by the
        # zone's issuer URL
        provider = ClientSecret({
            "https://zone1.keycard.cloud": ("client_id_1", "client_secret_1"),
            "https://zone2.keycard.cloud": ("client_id_2", "client_secret_2"),
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
            self.auth = MultiZoneBasicAuth(issuer_credentials=credentials)
        else:
            raise ClientSecretConfigurationError(
                credentials_type=type(credentials).__name__
            )

    @property
    def is_multi_zone(self) -> bool:
        """Whether this credential holds per-zone credentials."""
        return isinstance(self.auth, MultiZoneBasicAuth)

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

    _DEFAULT_STORAGE_DIR = "./server_keys"
    _LEGACY_STORAGE_DIR = "./mcp_keys"

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
            resolved_dir = storage_dir or self._resolve_default_storage_dir()
            self._storage = FilePrivateKeyStorage(resolved_dir)

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

    @classmethod
    def _resolve_default_storage_dir(cls) -> str:
        # Prefer the new default. Fall back to the pre-extraction directory
        # (./mcp_keys) when it exists and the new one does not, so services
        # that relied on the implicit default keep their existing keys after
        # upgrade. This fallback will be removed in a future release.
        if not os.path.isdir(cls._DEFAULT_STORAGE_DIR) and os.path.isdir(
            cls._LEGACY_STORAGE_DIR
        ):
            warnings.warn(
                f"WebIdentity is using legacy storage directory "
                f"{cls._LEGACY_STORAGE_DIR!r} because no storage_dir was "
                f"provided and {cls._DEFAULT_STORAGE_DIR!r} does not exist. "
                f"Pass storage_dir={cls._LEGACY_STORAGE_DIR!r} explicitly to "
                f"silence this warning, or migrate keys to "
                f"{cls._DEFAULT_STORAGE_DIR!r} (the new default).",
                DeprecationWarning,
                stacklevel=3,
            )
            return cls._LEGACY_STORAGE_DIR
        return cls._DEFAULT_STORAGE_DIR

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

    def get_client_jwks_url(self, resource_server_url: str) -> str:
        """Return the client's published JWKS URL for the given resource server.

        The authorization server fetches this URL to obtain the public key that
        verifies the ``private_key_jwt`` client assertions this credential signs.
        """
        return self.identity_manager.get_client_jwks_url(resource_server_url)

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


# Source identifiers carried on WorkloadIdentityConfigurationError and
# WorkloadIdentityRuntimeError, for branching on which token source failed.
WORKLOAD_IDENTITY_SOURCE_FILE = "file"
WORKLOAD_IDENTITY_SOURCE_GCP_METADATA = "gcp-metadata"
WORKLOAD_IDENTITY_SOURCE_FLY = "fly"
WORKLOAD_IDENTITY_SOURCE_CUSTOM = "custom"


class IdentityTokenSource(Protocol):
    """Supplies a platform-signed OIDC token for use as a client assertion.

    The only per-platform piece of a workload identity credential:
    FileTokenSource covers platforms that project the token to a file (EKS,
    AKS, Kubernetes projected service-account tokens), GCPMetadataTokenSource
    covers platforms that serve it from the GCP metadata endpoint (GKE, GCE,
    Cloud Run), FlyTokenSource covers Fly Machines, and any bare callable
    returning the token (sync or async) is accepted as a source.

    identity_token is called on every token exchange. Implementations must
    return the current token; platforms rotate these tokens, so returning a
    stale cached value risks an expired assertion.
    """

    async def identity_token(self) -> str:
        """Return the current platform-signed OIDC token."""
        ...


class FileTokenSource:
    """Reads a platform-projected OIDC token from a mounted file.

    The file is re-read on every call (platforms rotate projected tokens).
    Covers EKS pod identity, AKS workload identity, any Kubernetes projected
    service-account token, and CI providers that write the token to a file.

    Environment variable discovery (when token_file_path is not provided):
        1. The variable named by env_var_name, when given
        2. KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE
        3. AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE
        4. AWS_WEB_IDENTITY_TOKEN_FILE
        5. AZURE_FEDERATED_TOKEN_FILE
    """

    default_env_var_names = [
        "KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AZURE_FEDERATED_TOKEN_FILE",
    ]

    def __init__(
        self,
        token_file_path: str | None = None,
        env_var_name: str | None = None,
    ):
        if token_file_path is None:
            env_names = (
                self.default_env_var_names
                if env_var_name is None
                else [env_var_name, *self.default_env_var_names]
            )
            token_file_path = next(
                (os.environ.get(name) for name in env_names if os.environ.get(name)),
                None,
            )
            if not token_file_path:
                raise WorkloadIdentityConfigurationError(
                    "Could not find token file path in environment variables; "
                    f"checked: {', '.join(env_names)}",
                    source=WORKLOAD_IDENTITY_SOURCE_FILE,
                )

        self.token_file_path = token_file_path
        self._read(WorkloadIdentityConfigurationError)

    def _read(self, error_cls: type) -> str:
        try:
            with open(self.token_file_path) as f:
                token = f.read().strip()
        except OSError as err:
            raise error_cls(
                f"Error reading token file: {self.token_file_path}",
                source=WORKLOAD_IDENTITY_SOURCE_FILE,
            ) from err
        if not token:
            raise error_cls(
                f"Token file is empty: {self.token_file_path}",
                source=WORKLOAD_IDENTITY_SOURCE_FILE,
            )
        return token

    async def identity_token(self) -> str:
        """Re-read the token file and return its stripped contents."""
        return self._read(WorkloadIdentityRuntimeError)


class GCPMetadataTokenSource:
    """Fetches an OIDC identity token from the GCP metadata server.

    Requests a token for the default service account with the given audience,
    typically the Keycard zone URL. Covers GKE, GCE, and Cloud Run.
    """

    _IDENTITY_PATH = "/computeMetadata/v1/instance/service-accounts/default/identity"

    def __init__(
        self,
        audience: str,
        metadata_url: str = "http://metadata.google.internal",
        timeout: float = 5.0,
        _transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not audience or not audience.strip():
            raise WorkloadIdentityConfigurationError(
                "audience must not be empty",
                source=WORKLOAD_IDENTITY_SOURCE_GCP_METADATA,
            )
        self.audience = audience
        self.metadata_url = metadata_url.rstrip("/")
        self.timeout = timeout
        self._transport = _transport

    async def identity_token(self) -> str:
        """Request a GCP-signed OIDC JWT from the metadata server."""
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self._transport
            ) as client:
                response = await client.get(
                    self.metadata_url + self._IDENTITY_PATH,
                    params={"audience": self.audience, "format": "full"},
                    headers={"Metadata-Flavor": "Google"},
                )
        except httpx.HTTPError as err:
            raise WorkloadIdentityRuntimeError(
                f"Error calling metadata server at {self.metadata_url} "
                "(is this running on GCP?)",
                source=WORKLOAD_IDENTITY_SOURCE_GCP_METADATA,
            ) from err

        if response.status_code != 200:
            raise WorkloadIdentityRuntimeError(
                f"Metadata server returned status {response.status_code}",
                source=WORKLOAD_IDENTITY_SOURCE_GCP_METADATA,
            )
        token = response.text.strip()
        if not token:
            raise WorkloadIdentityRuntimeError(
                "Metadata server returned an empty token",
                source=WORKLOAD_IDENTITY_SOURCE_GCP_METADATA,
            )
        return token


class FlyTokenSource:
    """Fetches an OIDC token from the Fly.io Machines API over the local Unix socket.

    Covers workloads running on Fly Machines. The socket is not probed at
    construction; an unreachable Machines API surfaces as a
    WorkloadIdentityRuntimeError at the first fetch.
    """

    _TOKEN_URL = "http://localhost/v1/tokens/oidc"
    _TIMEOUT = 5.0

    def __init__(
        self,
        audience: str | None = None,
        socket_path: str = "/.fly/api",
        _transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.audience = audience
        self.socket_path = socket_path
        self._transport = _transport

    async def identity_token(self) -> str:
        """Request a Fly-signed OIDC JWT from the Machines API."""
        transport = self._transport or httpx.AsyncHTTPTransport(uds=self.socket_path)
        payload = {"aud": self.audience} if self.audience else {}
        try:
            async with httpx.AsyncClient(
                transport=transport, timeout=self._TIMEOUT
            ) as client:
                response = await client.post(self._TOKEN_URL, json=payload)
        except httpx.HTTPError as err:
            raise WorkloadIdentityRuntimeError(
                f"Error calling Machines API socket {self.socket_path} "
                "(is this running on a Fly Machine?)",
                source=WORKLOAD_IDENTITY_SOURCE_FLY,
            ) from err

        if response.status_code != 200:
            raise WorkloadIdentityRuntimeError(
                f"Machines API returned status {response.status_code}",
                source=WORKLOAD_IDENTITY_SOURCE_FLY,
            )
        token = response.text.strip()
        if not token:
            raise WorkloadIdentityRuntimeError(
                "Machines API returned an empty token",
                source=WORKLOAD_IDENTITY_SOURCE_FLY,
            )
        return token


class WorkloadIdentity:
    """Workload identity provider using a platform-signed OIDC token.

    On every token exchange it fetches the current token from the source and
    attaches it as a jwt-bearer client assertion. It holds no shared secret
    and never caches the token across requests.

    Example:
        # EKS / AKS / Kubernetes projected token (path discovered from env)
        provider = WorkloadIdentity(FileTokenSource())

        # GKE / GCE / Cloud Run
        provider = WorkloadIdentity(
            GCPMetadataTokenSource(audience="https://zone.keycard.cloud")
        )

        # Custom fetch
        provider = WorkloadIdentity(my_async_fetch)

    Args:
        source: A IdentityTokenSource, or a bare callable (sync or async)
            returning the token.
        client_id: Optional ID of the Keycard application credential this
            workload authenticates as, sent as the client_id form parameter
            alongside the assertion. Token-federation application credentials
            are resolved by this ID, so they require it; legacy token
            credentials are resolved by the assertion's subject and do not
            use it.
    """

    def __init__(
        self,
        source: "IdentityTokenSource | Callable[[], Awaitable[str] | str]",
        client_id: str | None = None,
    ):
        if source is None:
            raise WorkloadIdentityConfigurationError(
                "identity token source must not be None"
            )
        if not callable(getattr(source, "identity_token", None)) and not callable(
            source
        ):
            raise WorkloadIdentityConfigurationError(
                "identity token source must provide identity_token() or be callable"
            )
        self._source = source
        self.client_id = client_id

    async def _fetch_identity_token(self) -> str:
        fetch = getattr(self._source, "identity_token", None)
        if not callable(fetch):
            fetch = self._source
        try:
            result = fetch()
            token = await result if inspect.isawaitable(result) else result
        except (WorkloadIdentityConfigurationError, WorkloadIdentityRuntimeError):
            raise
        except Exception as err:
            raise WorkloadIdentityRuntimeError(
                "Error fetching identity token",
                source=WORKLOAD_IDENTITY_SOURCE_CUSTOM,
            ) from err
        if not isinstance(token, str) or not token.strip():
            raise WorkloadIdentityRuntimeError(
                "Identity token source returned an empty token",
                source=WORKLOAD_IDENTITY_SOURCE_CUSTOM,
            )
        return token

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
        assertion = await self._fetch_identity_token()

        return TokenExchangeRequest(
            subject_token=subject_token,
            resource=resource,
            subject_token_type="urn:ietf:params:oauth:token-type:access_token",
            client_assertion_type=GrantType.JWT_BEARER_CLIENT_ASSERTION,
            client_assertion=assertion,
            client_id=self.client_id,
        )


class EKSWorkloadIdentity(WorkloadIdentity):
    """EKS workload identity provider using mounted tokens.

    Deprecated: use WorkloadIdentity with FileTokenSource, which also covers
    AKS and other platforms that project token files.

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
        "KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE",
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
        super().__init__(source=self._read_token_async)

    async def _read_token_async(self) -> str:
        return self._read_token()

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

