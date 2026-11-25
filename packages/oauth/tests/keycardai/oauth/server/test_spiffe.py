"""SPIFFE rows 11 to 16 of specs/application-credentials/workload-identity.md."""

import subprocess
import sys

import grpc
import pytest
import pytest_asyncio

from keycardai.oauth.server import SpiffeJwtSvidSource
from keycardai.oauth.server._spiffe_workload_pb2 import (
    JWTSVID,
    JWTSVIDRequest,
    JWTSVIDResponse,
)
from keycardai.oauth.server.exceptions import (
    WorkloadIdentityConfigurationError,
    WorkloadIdentityRuntimeError,
)

AUDIENCE = "https://zone.keycard.cloud"
SPIFFE_ID = "spiffe://example.org/workload"


@pytest_asyncio.fixture
async def workload_api(tmp_path):
    requests = []
    svids = [
        JWTSVID(spiffe_id=SPIFFE_ID, svid="opaque.header.signature"),
        JWTSVID(spiffe_id="spiffe://example.org/other", svid="another.opaque.token"),
    ]

    async def fetch(request, context):
        requests.append((request, dict(context.invocation_metadata())))
        return JWTSVIDResponse(
            svids=[svid for svid in svids if request.spiffe_id in ("", svid.spiffe_id)]
        )

    socket = tmp_path / "agent.sock"
    server = grpc.aio.server()
    handler = grpc.unary_unary_rpc_method_handler(
        fetch,
        request_deserializer=JWTSVIDRequest.FromString,
        response_serializer=JWTSVIDResponse.SerializeToString,
    )
    server.add_generic_rpc_handlers(
        (grpc.method_handlers_generic_handler(
            "SpiffeWorkloadAPI", {"FetchJWTSVID": handler}
        ),)
    )
    server.add_insecure_port(f"unix:{socket}")
    await server.start()
    try:
        yield socket, requests, svids
    finally:
        await server.stop(None)


@pytest.mark.asyncio
async def test_row_11_does_not_open_a_channel_at_construction(monkeypatch, workload_api):
    socket, requests, _ = workload_api
    real_channel = grpc.aio.insecure_channel
    targets = []

    def open_channel(target):
        targets.append(target)
        return real_channel(target)

    monkeypatch.setattr(grpc.aio, "insecure_channel", open_channel)
    source = SpiffeJwtSvidSource(AUDIENCE, socket_path=socket)
    assert targets == []
    assert await source.identity_token() == "opaque.header.signature"
    assert targets == [f"unix:{socket}"]
    assert len(requests) == 1


@pytest.mark.parametrize("kwargs", [{}, {"audience": None}, {"audience": "  "}])
def test_row_12_missing_audience(kwargs):
    with pytest.raises(WorkloadIdentityConfigurationError, match="audience") as error:
        SpiffeJwtSvidSource(**kwargs)
    assert error.value.source == "spiffe"


@pytest.mark.asyncio
async def test_row_13_socket_is_discovered_at_fetch(monkeypatch, workload_api):
    socket, _, _ = workload_api
    monkeypatch.delenv("SPIFFE_ENDPOINT_SOCKET", raising=False)
    source = SpiffeJwtSvidSource(AUDIENCE)
    with pytest.raises(
        WorkloadIdentityConfigurationError, match="SPIFFE_ENDPOINT_SOCKET"
    ) as error:
        await source.identity_token()
    assert error.value.source == "spiffe"

    monkeypatch.setenv("SPIFFE_ENDPOINT_SOCKET", f"unix://{socket}")
    assert await source.identity_token() == "opaque.header.signature"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("spiffe_id", "expected"),
    [
        (None, "opaque.header.signature"),
        ("spiffe://example.org/other", "another.opaque.token"),
    ],
)
async def test_row_14_fetch_returns_opaque_jwt(
    monkeypatch, workload_api, spiffe_id, expected
):
    socket, requests, _ = workload_api
    monkeypatch.setenv("SPIFFE_ENDPOINT_SOCKET", "unix:/not-the-agent.sock")
    source = SpiffeJwtSvidSource(AUDIENCE, socket_path=socket, spiffe_id=spiffe_id)

    assert await source.identity_token() == expected
    [(request, metadata)] = requests
    assert list(request.audience) == [AUDIENCE]
    assert request.spiffe_id == (spiffe_id or "")
    assert metadata["workload.spiffe.io"] == "true"


@pytest.mark.asyncio
async def test_row_15_unreachable_agent_is_retryable(tmp_path):
    source = SpiffeJwtSvidSource(
        AUDIENCE, socket_path=tmp_path / "absent.sock", init_timeout=0.1
    )
    with pytest.raises(WorkloadIdentityRuntimeError, match="could not issue") as error:
        await source.identity_token()
    assert error.value.source == "spiffe"
    assert error.value.retryable is True
    cause = error.value.__cause__
    assert isinstance(cause, grpc.aio.AioRpcError)
    assert cause.code() == grpc.StatusCode.DEADLINE_EXCEEDED


@pytest.mark.asyncio
async def test_row_15_no_svid_for_requested_identity_is_retryable(workload_api):
    socket, _, _ = workload_api
    source = SpiffeJwtSvidSource(
        AUDIENCE, socket_path=socket, spiffe_id="spiffe://example.org/missing"
    )
    with pytest.raises(WorkloadIdentityRuntimeError, match="no JWT-SVID") as error:
        await source.identity_token()
    assert error.value.source == "spiffe"
    assert error.value.retryable is True
    assert isinstance(error.value.__cause__, IndexError)


@pytest.mark.asyncio
async def test_empty_token_is_a_non_retryable_runtime_error(workload_api):
    socket, _, svids = workload_api
    svids[0].svid = ""
    source = SpiffeJwtSvidSource(AUDIENCE, socket_path=socket)
    with pytest.raises(WorkloadIdentityRuntimeError, match="empty JWT-SVID") as error:
        await source.identity_token()
    assert error.value.source == "spiffe"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_row_16_returns_rotated_svid_on_next_fetch(workload_api):
    socket, requests, svids = workload_api
    source = SpiffeJwtSvidSource(AUDIENCE, socket_path=socket)
    assert await source.identity_token() == "opaque.header.signature"
    svids[0].svid = "rotated.header.signature"
    assert await source.identity_token() == "rotated.header.signature"
    assert len(requests) == 2


def test_core_import_and_construction_do_not_import_spiffe_dependencies():
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio
import sys

class BlockOptionalImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"grpc", "spiffe", "google"}:
            raise ModuleNotFoundError(fullname)

sys.meta_path.insert(0, BlockOptionalImports())
import keycardai.oauth
from keycardai.oauth.server import SpiffeJwtSvidSource
from keycardai.oauth.server.exceptions import WorkloadIdentityConfigurationError

source = SpiffeJwtSvidSource("audience", socket_path="/agent.sock")
try:
    asyncio.run(source.identity_token())
except WorkloadIdentityConfigurationError as error:
    assert "keycardai-oauth[spiffe]" in str(error)
    assert error.source == "spiffe"
    assert isinstance(error.__cause__, ModuleNotFoundError)
else:
    raise AssertionError("Expected a missing extra configuration error")
""",
        ],
        check=True,
    )
