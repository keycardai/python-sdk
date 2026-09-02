"""Tests for discover_credential, the env-based ApplicationCredential factory."""

import pytest

from keycardai.oauth import BasicAuth
from keycardai.oauth.server import discover_credential
from keycardai.oauth.server.credentials import (
    ClientSecret,
    FileTokenSource,
    WorkloadIdentity,
)
from keycardai.oauth.server.exceptions import (
    CredentialDiscoveryError,
    WorkloadIdentityConfigurationError,
)

ALL_VARS = [
    "KEYCARD_CLIENT_ID",
    "KEYCARD_CLIENT_SECRET",
    "KEYCARD_APPLICATION_CREDENTIAL_TYPE",
    *FileTokenSource.default_env_var_names,
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ALL_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "token"
    path.write_text("platform-signed-jwt")
    return str(path)


def test_client_secret_from_env(monkeypatch):
    monkeypatch.setenv("KEYCARD_CLIENT_ID", "cid")
    monkeypatch.setenv("KEYCARD_CLIENT_SECRET", "csecret")
    credential = discover_credential()
    assert isinstance(credential, ClientSecret)
    assert isinstance(credential.auth, BasicAuth)
    assert credential.auth.client_id == "cid"
    assert credential.auth.client_secret == "csecret"


@pytest.mark.asyncio
@pytest.mark.parametrize("var", FileTokenSource.default_env_var_names)
async def test_workload_identity_from_each_token_file_var(monkeypatch, token_file, var):
    monkeypatch.setenv(var, token_file)
    credential = discover_credential()
    assert isinstance(credential, WorkloadIdentity)
    assert credential.client_id is None
    assert await credential._fetch_identity_token() == "platform-signed-jwt"


def test_workload_identity_picks_up_client_id_without_secret(monkeypatch, token_file):
    monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", token_file)
    monkeypatch.setenv("KEYCARD_CLIENT_ID", "cid")
    credential = discover_credential()
    assert isinstance(credential, WorkloadIdentity)
    assert credential.client_id == "cid"


def test_empty_environment_is_rejected():
    with pytest.raises(CredentialDiscoveryError, match="No application credential"):
        discover_credential()


def test_client_id_alone_is_treated_as_absent(monkeypatch):
    monkeypatch.setenv("KEYCARD_CLIENT_ID", "cid")
    with pytest.raises(CredentialDiscoveryError, match="No application credential"):
        discover_credential()


def test_client_secret_without_id_is_rejected(monkeypatch):
    monkeypatch.setenv("KEYCARD_CLIENT_SECRET", "csecret")
    with pytest.raises(CredentialDiscoveryError, match="without KEYCARD_CLIENT_ID"):
        discover_credential()


def test_ambiguous_environment_is_rejected(monkeypatch, token_file):
    monkeypatch.setenv("KEYCARD_CLIENT_ID", "cid")
    monkeypatch.setenv("KEYCARD_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", token_file)
    with pytest.raises(CredentialDiscoveryError, match="Ambiguous") as exc_info:
        discover_credential()
    assert exc_info.value.resolvable == ["client_secret", "workload_identity"]


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("client_secret", ClientSecret),
        ("workload_identity", WorkloadIdentity),
        ("eks_workload_identity", WorkloadIdentity),
    ],
)
def test_explicit_type_resolves_ambiguity(monkeypatch, token_file, requested, expected):
    monkeypatch.setenv("KEYCARD_CLIENT_ID", "cid")
    monkeypatch.setenv("KEYCARD_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", token_file)
    monkeypatch.setenv("KEYCARD_APPLICATION_CREDENTIAL_TYPE", requested)
    assert isinstance(discover_credential(), expected)


def test_explicit_workload_identity_prefers_keycard_token_file(monkeypatch, tmp_path):
    keycard_file = tmp_path / "keycard"
    keycard_file.write_text("keycard-token")
    other_file = tmp_path / "aws"
    other_file.write_text("aws-token")
    monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", str(other_file))
    monkeypatch.setenv("KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE", str(keycard_file))
    monkeypatch.setenv("KEYCARD_APPLICATION_CREDENTIAL_TYPE", "eks_workload_identity")
    credential = discover_credential()
    assert isinstance(credential, WorkloadIdentity)
    assert credential._source.token_file_path == str(keycard_file)


def test_unknown_credential_type_is_rejected(monkeypatch):
    monkeypatch.setenv("KEYCARD_APPLICATION_CREDENTIAL_TYPE", "web_identity")
    with pytest.raises(CredentialDiscoveryError, match="web_identity"):
        discover_credential()


def test_unknown_credential_type_wins_over_valid_client_secret(monkeypatch):
    monkeypatch.setenv("KEYCARD_CLIENT_ID", "cid")
    monkeypatch.setenv("KEYCARD_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("KEYCARD_APPLICATION_CREDENTIAL_TYPE", "vault")
    with pytest.raises(CredentialDiscoveryError, match="Unknown .*vault"):
        discover_credential()


def test_explicit_client_secret_without_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("KEYCARD_APPLICATION_CREDENTIAL_TYPE", "client_secret")
    monkeypatch.setenv("KEYCARD_CLIENT_ID", "cid")
    with pytest.raises(CredentialDiscoveryError, match="requires both"):
        discover_credential()


def test_explicit_workload_identity_without_token_file_is_rejected(monkeypatch):
    monkeypatch.setenv("KEYCARD_APPLICATION_CREDENTIAL_TYPE", "workload_identity")
    with pytest.raises(CredentialDiscoveryError, match="requires a token file"):
        discover_credential()


def test_missing_token_file_surfaces_workload_identity_error(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", str(tmp_path / "absent"))
    with pytest.raises(WorkloadIdentityConfigurationError):
        discover_credential()


def test_explicit_env_mapping_is_used_instead_of_os_environ(monkeypatch):
    monkeypatch.setenv("KEYCARD_CLIENT_ID", "from-os")
    monkeypatch.setenv("KEYCARD_CLIENT_SECRET", "from-os")
    credential = discover_credential(
        {"KEYCARD_CLIENT_ID": "explicit", "KEYCARD_CLIENT_SECRET": "explicit"}
    )
    assert isinstance(credential, ClientSecret)
    assert credential.auth.client_id == "explicit"
