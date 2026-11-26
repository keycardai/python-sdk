"""``hermes keycard login`` and ``hermes keycard token`` rows."""

from __future__ import annotations

import asyncio
import json
import stat
import warnings
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs

import pytest

from keycardai.hermes import cli, grants
from keycardai.oauth import AsyncClient, LegacyGrantCredentialWarning
from keycardai.oauth.http._wire import HttpRequest, HttpResponse
from keycardai.oauth.types.oauth import GrantType, TokenEndpointAuthMethod

from .conftest import RESOURCE, SIGN_IN_RESOURCE, ZONE, Harness, base_settings


def files_under(path):
    return sorted(p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file())


def test_login_and_refresh_use_public_grants_without_legacy_warnings(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant_forms = []

    class Transport:
        async def request_raw(self, req: HttpRequest, *, timeout: float | None = None) -> HttpResponse:
            if req.url == f"{ZONE}/.well-known/oauth-authorization-server":
                body = {
                    "issuer": ZONE,
                    "registration_endpoint": f"{ZONE}/register",
                    "device_authorization_endpoint": f"{ZONE}/device",
                    "token_endpoint": f"{ZONE}/token",
                }
            elif req.url == f"{ZONE}/register":
                body = {"client_id": "public-1"}
            else:
                grant_forms.append(parse_qs((req.body or b"").decode()))
                if req.url == f"{ZONE}/device":
                    body = {
                        "device_code": "dev",
                        "user_code": "ABCD",
                        "verification_uri": f"{ZONE}/activate",
                        "expires_in": 600,
                        "interval": 1,
                    }
                else:
                    assert req.url == f"{ZONE}/token"
                    body = {
                        "access_token": "access-1",
                        "token_type": "Bearer",
                        "refresh_token": "refresh-1",
                        "expires_in": 600,
                    }
            return HttpResponse(201 if req.url == f"{ZONE}/register" else 200, {}, json.dumps(body).encode())

    def client_factory(issuer: str, **kwargs) -> AsyncClient:
        clock = Mock(now=Mock(return_value=datetime.now(UTC)), sleep=AsyncMock())
        return AsyncClient(issuer, transport=Transport(), clock=clock, **kwargs)

    monkeypatch.setattr(cli, "AsyncClient", client_factory)
    monkeypatch.setattr(grants, "AsyncClient", client_factory)
    harness.plugin.grants.public_client = None

    with warnings.catch_warnings():
        warnings.simplefilter("error", LegacyGrantCredentialWarning)
        assert cli.login(harness.plugin, None) == "owner"
        for client_id in ("public-2", "public-3"):
            harness.store_sign_in("owner", expires_in=10, client_id=client_id)
            assert harness.plugin.grants.fresh_sign_in("owner").access_token == "access-1"
        harness.store_sign_in("owner", expires_in=10, client_id="public-4")
        assert asyncio.run(harness.plugin.grants.afresh_sign_in("owner")).access_token == "access-1"

    assert [form["client_id"] for form in grant_forms] == [
        ["public-1"], ["public-1"], ["public-2"], ["public-3"], ["public-4"],
    ]
    assert all("client_secret" not in form for form in grant_forms)


# row 9
def test_row_9_login_registers_once_runs_the_device_flow_and_stores_0600(harness: Harness) -> None:
    before = files_under(harness.home)
    code, out, err = harness.cli("login", "--actor", "A")
    assert code == 0 and out == ""
    assert "ABCD" in err and "https://zone/device" in err
    assert len(harness.client.register_calls) == 1
    request = harness.client.register_calls[0]
    assert request.grant_types == [GrantType.DEVICE_CODE, GrantType.REFRESH_TOKEN]
    assert request.token_endpoint_auth_method == TokenEndpointAuthMethod.NONE
    assert harness.client.device_calls == [{"resources": [SIGN_IN_RESOURCE]}]
    assert harness.client.poll_calls == [{}]
    store = harness.plugin.grants.store
    path = store.path("A")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = json.loads(path.read_text())
    assert data["client_id"] == "public-1"
    assert data["refresh_token"] == "refresh-1" and data["access_token"] == "device-access"
    assert stat.S_IMODE((store.dir / "client.json").stat().st_mode) == 0o600
    added = sorted(set(files_under(harness.home)) - set(before))
    # The sign-in, its lock file (login saves under the lock) and the public client.
    expected = [path, path.with_suffix(".lock"), store.dir / "client.json"]
    assert added == sorted(p.relative_to(harness.home).as_posix() for p in expected)
    assert stat.S_IMODE(store.dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.dir.parent.stat().st_mode) == 0o700


# row 10
def test_row_10_verify_sign_in_mismatch_stores_nothing_and_names_neither_account(make_harness) -> None:
    h = make_harness(base_settings(verify_sign_in=True, actors={"A": "user-expected"}))
    h.client.userinfo_sub = "user-other"
    code, out, err = h.cli("login", "--actor", "A")
    assert code == 1 and out == ""
    assert "user-expected" not in err and "user-other" not in err
    assert "nothing was stored" in err
    assert h.plugin.grants.store.load("A") is None
    assert h.client.userinfo_calls == ["device-access"]


def test_verify_sign_in_match_stores(make_harness) -> None:
    h = make_harness(base_settings(verify_sign_in=True, actors={"A": "user-1"}))
    assert h.cli("login", "--actor", "A")[0] == 0
    assert h.plugin.grants.store.load("A") is not None


# row 22
def test_row_22_login_without_a_refresh_token_stores_nothing(harness: Harness) -> None:
    harness.client.device_refresh_token = None
    code, out, err = harness.cli("login", "--actor", "A")
    assert code == 1 and out == "" and "refresh token" in err
    assert harness.plugin.grants.store.load("A") is None


# row 23
def test_row_23_login_defaults_to_owner_actor_and_reuses_the_client(harness: Harness) -> None:
    assert harness.cli("login")[0] == 0
    assert harness.plugin.grants.store.load("owner") is not None
    assert harness.cli("login", "--actor", "B")[0] == 0
    assert len(harness.client.register_calls) == 1
    assert [c["resources"] for c in harness.client.device_calls] == [[SIGN_IN_RESOURCE], [SIGN_IN_RESOURCE]]


def test_login_stores_under_the_canonical_actor_after_aliases(make_harness) -> None:
    h = make_harness(base_settings(actor_aliases={"athena": "owner"}))
    assert h.cli("login", "--actor", "athena")[0] == 0
    assert h.plugin.grants.store.load("owner") is not None
    assert h.plugin.grants.store.load("athena") is None


def test_login_without_any_actor_fails(make_harness) -> None:
    settings = base_settings()
    del settings["owner_actor"]
    h = make_harness(settings)
    code, _, err = h.cli("login")
    assert code == 1 and "--actor" in err


# row 25
def test_row_25_token_writes_only_the_token_then_fails_cleanly(harness: Harness) -> None:
    harness.store_sign_in("owner", "owner-token")
    code, out, err = harness.cli("token", RESOURCE)
    assert (code, out, err) == (0, f"obo-owner-token-for-{RESOURCE}\n", "")
    assert harness.client.exchange_calls[0].subject_token == "owner-token"
    harness.client.denied_resources.add(RESOURCE)
    code, out, err = harness.cli("token", RESOURCE)
    assert code == 1 and out == "" and err.startswith("keycard: ")


def test_token_without_a_sign_in_fails_with_the_login_command(harness: Harness) -> None:
    code, out, err = harness.cli("token", RESOURCE, "--actor", "nobody")
    assert code == 1 and out == "" and "hermes keycard login --actor nobody" in err


# row 33
def test_row_33_token_as_self_uses_client_credentials_and_reads_no_sign_in(harness: Harness) -> None:
    code, out, err = harness.cli("token", RESOURCE, "--as-self")
    assert (code, out, err) == (0, f"self-for-{RESOURCE}\n", "")
    assert len(harness.client.self_calls) == 1
    assert harness.client.exchange_calls == [] and harness.client.refresh_calls == []
