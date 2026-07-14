"""Unit tests for the WorkloadIdentity credential and its token sources.

Follows the conformance table in the workload-identity spec
(keycard-sdk-spec, specs/application-credentials/workload-identity.md).
"""

import asyncio
import os
import shutil
import tempfile

import httpx
import pytest

from keycardai.oauth import NoneAuth
from keycardai.oauth.server.credentials import (
    EKSWorkloadIdentity,
    FileTokenSource,
    FlyTokenSource,
    GCPMetadataTokenSource,
    WorkloadIdentity,
)
from keycardai.oauth.server.exceptions import (
    WorkloadIdentityConfigurationError,
    WorkloadIdentityRuntimeError,
)


async def async_token_source() -> str:
    return "platform-token"


class TestWorkloadIdentity:
    def test_rejects_none_source(self):
        with pytest.raises(WorkloadIdentityConfigurationError):
            WorkloadIdentity(None)

    def test_rejects_non_callable_source(self):
        with pytest.raises(WorkloadIdentityConfigurationError):
            WorkloadIdentity(object())

    def test_no_basic_auth(self):
        provider = WorkloadIdentity(async_token_source)
        assert isinstance(provider.get_http_client_auth(), NoneAuth)

    @pytest.mark.asyncio
    async def test_prepares_request_from_source(self):
        provider = WorkloadIdentity(async_token_source)

        request = await provider.prepare_token_exchange_request(
            client=None,
            subject_token="subject-token",
            resource="https://resource.example.com",
        )

        assert request.client_assertion == "platform-token"
        assert (
            request.client_assertion_type
            == "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        )
        assert request.subject_token == "subject-token"
        assert request.subject_token_type == "urn:ietf:params:oauth:token-type:access_token"
        assert request.resource == "https://resource.example.com"
        assert request.client_id is None

    @pytest.mark.asyncio
    async def test_client_id_carried_when_configured(self):
        provider = WorkloadIdentity(async_token_source, client_id="acr_123")

        request = await provider.prepare_token_exchange_request(
            client=None,
            subject_token="subject-token",
            resource="https://resource.example.com",
        )

        assert request.client_id == "acr_123"

    @pytest.mark.asyncio
    async def test_sync_callable_accepted(self):
        provider = WorkloadIdentity(lambda: "sync-token")

        request = await provider.prepare_token_exchange_request(
            client=None,
            subject_token="subject-token",
            resource="https://resource.example.com",
        )

        assert request.client_assertion == "sync-token"

    @pytest.mark.asyncio
    async def test_fetches_fresh_token_every_exchange(self):
        calls = 0

        async def counting_source() -> str:
            nonlocal calls
            calls += 1
            return f"token-{calls}"

        provider = WorkloadIdentity(counting_source)
        for expected in (1, 2):
            request = await provider.prepare_token_exchange_request(
                client=None,
                subject_token="subject-token",
                resource="https://resource.example.com",
            )
            assert request.client_assertion == f"token-{expected}", (
                "the credential must not cache the token across exchanges"
            )
        assert calls == 2

    @pytest.mark.asyncio
    async def test_wraps_custom_source_error(self):
        cause = RuntimeError("socket unavailable")

        async def failing_source() -> str:
            raise cause

        provider = WorkloadIdentity(failing_source)
        with pytest.raises(WorkloadIdentityRuntimeError) as exc_info:
            await provider.prepare_token_exchange_request(
                client=None,
                subject_token="subject-token",
                resource="https://resource.example.com",
            )
        assert exc_info.value.source == "custom"
        assert exc_info.value.__cause__ is cause

    @pytest.mark.asyncio
    async def test_passes_through_typed_source_error(self):
        typed = WorkloadIdentityRuntimeError("token file is empty", source="file")

        async def failing_source() -> str:
            raise typed

        provider = WorkloadIdentity(failing_source)
        with pytest.raises(WorkloadIdentityRuntimeError) as exc_info:
            await provider.prepare_token_exchange_request(
                client=None,
                subject_token="subject-token",
                resource="https://resource.example.com",
            )
        assert exc_info.value is typed, "the source's own typed error must pass through"
        assert exc_info.value.source == "file"

    @pytest.mark.asyncio
    async def test_rejects_empty_token_from_source(self):
        provider = WorkloadIdentity(lambda: "   \n")

        with pytest.raises(WorkloadIdentityRuntimeError):
            await provider.prepare_token_exchange_request(
                client=None,
                subject_token="subject-token",
                resource="https://resource.example.com",
            )


class TestFileTokenSource:
    def _clear_discovery_env(self, monkeypatch):
        for name in FileTokenSource.default_env_var_names:
            monkeypatch.delenv(name, raising=False)

    @pytest.mark.asyncio
    async def test_explicit_path(self, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("projected-token\n")

        source = FileTokenSource(token_file_path=str(token_file))

        assert await source.subject_token() == "projected-token"

    def test_configuration_error_on_missing_file(self, tmp_path):
        with pytest.raises(WorkloadIdentityConfigurationError) as exc_info:
            FileTokenSource(token_file_path=str(tmp_path / "does-not-exist"))
        assert exc_info.value.source == "file"
        assert isinstance(exc_info.value.__cause__, FileNotFoundError)

    @pytest.mark.asyncio
    async def test_env_discovery_each_variable(self, tmp_path, monkeypatch):
        token_file = tmp_path / "token"
        token_file.write_text("discovered-token")

        for env_name in FileTokenSource.default_env_var_names:
            self._clear_discovery_env(monkeypatch)
            monkeypatch.setenv(env_name, str(token_file))

            source = FileTokenSource()
            assert await source.subject_token() == "discovered-token", (
                f"{env_name} must be discovered"
            )

    @pytest.mark.asyncio
    async def test_custom_env_var_wins(self, tmp_path, monkeypatch):
        custom_file = tmp_path / "custom"
        custom_file.write_text("custom-token")
        default_file = tmp_path / "default"
        default_file.write_text("default-token")

        self._clear_discovery_env(monkeypatch)
        monkeypatch.setenv("CUSTOM_TOKEN_FILE", str(custom_file))
        monkeypatch.setenv(
            "KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE", str(default_file)
        )

        source = FileTokenSource(env_var_name="CUSTOM_TOKEN_FILE")
        assert await source.subject_token() == "custom-token"

    def test_configuration_error_without_path_or_env(self, monkeypatch):
        self._clear_discovery_env(monkeypatch)

        with pytest.raises(WorkloadIdentityConfigurationError):
            FileTokenSource()

    @pytest.mark.asyncio
    async def test_fresh_read_after_rotation(self, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("initial-token")
        source = FileTokenSource(token_file_path=str(token_file))

        token_file.write_text("rotated-token")

        assert await source.subject_token() == "rotated-token"

    @pytest.mark.asyncio
    async def test_runtime_error_after_construction(self, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("initial-token")
        source = FileTokenSource(token_file_path=str(token_file))

        os.remove(token_file)

        with pytest.raises(WorkloadIdentityRuntimeError) as exc_info:
            await source.subject_token()
        assert exc_info.value.source == "file"


class TestEKSWorkloadIdentityCompat:
    def test_is_a_workload_identity(self, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("eks-token")

        provider = EKSWorkloadIdentity(token_file_path=str(token_file))

        assert isinstance(provider, WorkloadIdentity)

    def test_does_not_discover_azure_var(self, tmp_path, monkeypatch):
        # The deprecated EKS provider keeps the EKS-only discovery list; the
        # AKS variable is discovered only by FileTokenSource.
        token_file = tmp_path / "token"
        token_file.write_text("azure-token")

        for name in FileTokenSource.default_env_var_names:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", str(token_file))

        with pytest.raises(WorkloadIdentityConfigurationError):
            EKSWorkloadIdentity()

        source = FileTokenSource()
        assert source.token_file_path == str(token_file)


class TestGCPMetadataTokenSource:
    def test_requires_audience(self):
        with pytest.raises(WorkloadIdentityConfigurationError) as exc_info:
            GCPMetadataTokenSource("  ")
        assert exc_info.value.source == "gcp-metadata"

    @pytest.mark.asyncio
    async def test_request_shape(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["audience"] = request.url.params.get("audience")
            seen["format"] = request.url.params.get("format")
            seen["metadata_flavor"] = request.headers.get("Metadata-Flavor")
            return httpx.Response(200, text="gcp-identity-token\n")

        source = GCPMetadataTokenSource(
            "https://zone.example.com",
            _transport=httpx.MockTransport(handler),
        )

        token = await source.subject_token()

        assert token == "gcp-identity-token"
        assert seen["path"] == (
            "/computeMetadata/v1/instance/service-accounts/default/identity"
        )
        assert seen["audience"] == "https://zone.example.com"
        assert seen["format"] == "full"
        assert seen["metadata_flavor"] == "Google"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(404, text="not found"),
            httpx.Response(200, text="  \n"),
        ],
        ids=["non-200 status", "empty body"],
    )
    async def test_runtime_error_on_failure(self, response):
        source = GCPMetadataTokenSource(
            "https://zone.example.com",
            _transport=httpx.MockTransport(lambda request: response),
        )

        with pytest.raises(WorkloadIdentityRuntimeError) as exc_info:
            await source.subject_token()
        assert exc_info.value.source == "gcp-metadata"

    @pytest.mark.asyncio
    async def test_runtime_error_when_unreachable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        source = GCPMetadataTokenSource(
            "https://zone.example.com",
            _transport=httpx.MockTransport(handler),
        )

        with pytest.raises(WorkloadIdentityRuntimeError) as exc_info:
            await source.subject_token()
        assert exc_info.value.__cause__ is not None


class TestFlyTokenSource:
    @pytest.mark.asyncio
    async def test_request_shape_with_audience(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["content_type"] = request.headers.get("Content-Type")
            seen["body"] = request.content.decode()
            return httpx.Response(200, text="fly-oidc-token\n")

        source = FlyTokenSource(
            audience="https://zone.example.com",
            _transport=httpx.MockTransport(handler),
        )

        token = await source.subject_token()

        assert token == "fly-oidc-token"
        assert seen["method"] == "POST"
        assert seen["path"] == "/v1/tokens/oidc"
        assert seen["content_type"] == "application/json"
        assert seen["body"] == '{"aud":"https://zone.example.com"}'

    @pytest.mark.asyncio
    async def test_empty_object_body_without_audience(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content.decode()
            return httpx.Response(200, text="fly-oidc-token")

        source = FlyTokenSource(_transport=httpx.MockTransport(handler))

        await source.subject_token()

        assert seen["body"] == "{}"

    @pytest.mark.asyncio
    async def test_runtime_error_on_non_200(self):
        source = FlyTokenSource(
            _transport=httpx.MockTransport(
                lambda request: httpx.Response(404, text="machine not found")
            ),
        )

        with pytest.raises(WorkloadIdentityRuntimeError) as exc_info:
            await source.subject_token()
        assert exc_info.value.source == "fly"

    @pytest.mark.asyncio
    async def test_runtime_error_when_socket_missing(self, tmp_path):
        source = FlyTokenSource(socket_path=str(tmp_path / "no-such.sock"))

        with pytest.raises(WorkloadIdentityRuntimeError) as exc_info:
            await source.subject_token()
        assert exc_info.value.__cause__ is not None

    @pytest.mark.asyncio
    async def test_real_unix_socket(self):
        # Prove the uds wiring end to end with a minimal HTTP server on a
        # real Unix socket. Uses tempfile.mkdtemp rather than tmp_path to
        # stay under the platform's Unix socket path length limit.
        socket_dir = tempfile.mkdtemp(prefix="fly")
        socket_path = os.path.join(socket_dir, "api.sock")
        body = b"fly-oidc-token"
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n" + body
        )

        async def serve(reader, writer):
            await reader.read(4096)
            writer.write(response)
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(serve, path=socket_path)
        try:
            source = FlyTokenSource(socket_path=socket_path)
            assert await source.subject_token() == "fly-oidc-token"
        finally:
            server.close()
            await server.wait_closed()
            shutil.rmtree(socket_dir, ignore_errors=True)
