"""SPIFFE JWT-SVID workload identity source."""

import math
import os

from .exceptions import WorkloadIdentityConfigurationError, WorkloadIdentityRuntimeError

_SOURCE = "spiffe"
_SOCKET_ENV = "SPIFFE_ENDPOINT_SOCKET"
_FETCH_METHOD = "/SpiffeWorkloadAPI/FetchJWTSVID"


class SpiffeJwtSvidSource:
    """Fetch opaque JWT-SVIDs from the SPIFFE Workload API on a Unix socket.

    Install `keycardai-oauth[spiffe]` to use this source. The socket defaults
    to `SPIFFE_ENDPOINT_SOCKET` and is resolved on fetch. Each fetch opens its
    own connection and waits at most `init_timeout` seconds for the RPC.
    Set `spiffe_id` if the workload holds multiple identities; otherwise the
    source uses the first JWT-SVID the agent returns.
    """

    def __init__(
        self,
        audience: str | None = None,
        *,
        spiffe_id: str | None = None,
        socket_path: str | os.PathLike[str] | None = None,
        init_timeout: float = 10.0,
    ):
        if not audience or not audience.strip():
            raise WorkloadIdentityConfigurationError(
                "audience must not be empty", source=_SOURCE
            )
        if not math.isfinite(init_timeout) or init_timeout <= 0:
            raise WorkloadIdentityConfigurationError(
                "init_timeout must be a positive duration in seconds", source=_SOURCE
            )
        self.audience = audience
        self.spiffe_id = spiffe_id
        self.socket_path = socket_path
        self.init_timeout = init_timeout

    def _socket_target(self) -> str:
        socket = os.getenv(_SOCKET_ENV) if self.socket_path is None else self.socket_path
        if not socket:
            raise WorkloadIdentityConfigurationError(
                f"Set socket_path or {_SOCKET_ENV} for the SPIFFE Workload API",
                source=_SOURCE,
            )
        path = os.fspath(socket).removeprefix("unix:")
        if not path.startswith("/"):
            raise WorkloadIdentityConfigurationError(
                "The SPIFFE Workload API socket must be an absolute Unix socket path",
                source=_SOURCE,
            )
        return f"unix:/{path.lstrip('/')}"

    async def identity_token(self) -> str:
        """Return the current compact JWT-SVID without parsing it."""
        target = self._socket_target()
        try:
            from grpc import RpcError, aio

            from ._spiffe_workload_pb2 import JWTSVIDRequest, JWTSVIDResponse
        except ImportError as err:
            raise WorkloadIdentityConfigurationError(
                "Install keycardai-oauth[spiffe] to use SpiffeJwtSvidSource",
                source=_SOURCE,
            ) from err

        request = JWTSVIDRequest(audience=[self.audience], spiffe_id=self.spiffe_id)
        try:
            async with aio.insecure_channel(target) as channel:
                fetch = channel.unary_unary(
                    _FETCH_METHOD,
                    request_serializer=JWTSVIDRequest.SerializeToString,
                    response_deserializer=JWTSVIDResponse.FromString,
                )
                response = await fetch(
                    request,
                    timeout=self.init_timeout,
                    wait_for_ready=True,
                    metadata=(("workload.spiffe.io", "true"),),
                )
        except RpcError as err:
            raise WorkloadIdentityRuntimeError(
                "The SPIFFE Workload API could not issue a JWT-SVID",
                source=_SOURCE,
                retryable=True,
            ) from err

        try:
            token = response.svids[0].svid
        except IndexError as err:
            raise WorkloadIdentityRuntimeError(
                "The SPIFFE Workload API returned no JWT-SVID",
                source=_SOURCE,
                retryable=True,
            ) from err
        if not token.strip():
            raise WorkloadIdentityRuntimeError(
                "The SPIFFE Workload API returned an empty JWT-SVID",
                source=_SOURCE,
                retryable=False,
            )
        return token
