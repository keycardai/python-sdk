"""Runtime DeprecationWarning tests for the legacy bearer surface.

The deprecated symbols (`BearerAuthMiddleware`, `verify_bearer_token`)
are retained as shims so `keycardai-mcp` and `keycardai-agents` keep
working until they migrate to `KeycardAuthBackend` +
`AuthenticationMiddleware`. Until those migrations land, downstream
users importing the deprecated symbols should see a runtime warning
pointing at the replacement.
"""

import warnings
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from keycardai.starlette.middleware.bearer import (
    BearerAuthMiddleware,
    verify_bearer_token,
)


def _stub_verifier() -> MagicMock:
    token = MagicMock(token="verified-token", client_id="test-client", scopes=[])
    return MagicMock(
        enable_multi_zone=False,
        verify_token=AsyncMock(return_value=token),
        verify_token_for_zone=AsyncMock(return_value=token),
    )


def test_bearer_auth_middleware_init_warns():
    with pytest.warns(
        DeprecationWarning,
        match=r"BearerAuthMiddleware.*KeycardAuthBackend",
    ):
        BearerAuthMiddleware(MagicMock(), _stub_verifier())


@pytest.mark.asyncio
async def test_verify_bearer_token_call_warns():
    """Direct external call to verify_bearer_token fires the warning.

    The warning fires before any request introspection; catch it explicitly
    so post-warning failures (the MagicMock request not satisfying pydantic
    URL parsing) don't mask the assertion under test.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            await verify_bearer_token(MagicMock(), _stub_verifier())
        except Exception:
            pass

    matches = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "verify_bearer_token" in str(w.message)
        and "KeycardAuthBackend" in str(w.message)
    ]
    assert len(matches) == 1, f"Expected exactly one warning, got {matches}"


def test_middleware_dispatch_does_not_double_warn():
    """A single BearerAuthMiddleware instance must fire exactly one warning.

    The internal `dispatch` path must call `verify_bearer_token` with
    `_from_middleware=True` so the per-request flow does not emit a second
    warning beyond the one fired at middleware construction.
    """
    async def endpoint(request):
        return PlainTextResponse("ok")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app = Starlette(
            routes=[Route("/api/me", endpoint)],
            middleware=[Middleware(BearerAuthMiddleware, verifier=_stub_verifier())],
        )
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/api/me", headers={"Authorization": "Bearer some-token"})

    bearer_warnings = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "BearerAuthMiddleware" in str(w.message)
    ]
    verify_warnings = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "verify_bearer_token" in str(w.message)
    ]
    assert len(bearer_warnings) == 1, (
        f"Expected exactly one BearerAuthMiddleware warning, got {len(bearer_warnings)}"
    )
    assert verify_warnings == [], (
        f"verify_bearer_token must not warn when called from dispatch; got {verify_warnings}"
    )
