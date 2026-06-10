"""Unit tests for AccessContext.

Covers the rich-error context surfaced through `access()` and the
get_resource_error getter.
"""

import pytest

from keycardai.oauth.server.access_context import AccessContext
from keycardai.oauth.server.exceptions import ResourceAccessError
from keycardai.oauth.types.models import TokenResponse


def _token() -> TokenResponse:
    return TokenResponse(access_token="tok", token_type="bearer")


def test_access_returns_token_when_present():
    ctx = AccessContext()
    ctx.set_token("https://api.example.com", _token())
    assert ctx.access("https://api.example.com").access_token == "tok"


def test_access_carries_missing_token_context():
    ctx = AccessContext()
    ctx.set_token("https://api.example.com", _token())

    with pytest.raises(ResourceAccessError) as exc_info:
        ctx.access("https://missing.example.com")

    err = exc_info.value
    assert err.details["error_type"] == "missing_token"
    assert err.details["requested_resource"] == "https://missing.example.com"
    assert set(err.details["available_resources"]) == {"https://api.example.com"}


def test_access_carries_resource_error_context():
    ctx = AccessContext()
    detail = {"message": "denied by AS", "code": "access_denied"}
    ctx.set_resource_error("https://api.example.com", detail)

    with pytest.raises(ResourceAccessError) as exc_info:
        ctx.access("https://api.example.com")

    err = exc_info.value
    assert err.details["error_type"] == "resource_error"
    assert err.details["requested_resource"] == "https://api.example.com"
    assert err.details["error_details"] == detail
    assert err.details["available_resources"] is None


def test_access_carries_global_error_context():
    ctx = AccessContext()
    detail = {"message": "token exchange failed"}
    ctx.set_error(detail)

    with pytest.raises(ResourceAccessError) as exc_info:
        ctx.access("https://api.example.com")

    err = exc_info.value
    assert err.details["error_type"] == "global_error"
    assert err.details["requested_resource"] == "https://api.example.com"
    assert err.details["error_details"] == detail
    assert err.details["available_resources"] is None


def test_get_resource_error_returns_stored_detail_or_none():
    ctx = AccessContext()
    ctx.set_resource_error("https://api.example.com", {"message": "transient"})
    assert ctx.get_resource_error("https://api.example.com") == {"message": "transient"}
    assert ctx.get_resource_error("https://other.example.com") is None


def test_status_transitions():
    ctx = AccessContext()
    assert ctx.get_status() == "success"

    ctx.set_resource_error("https://api.example.com", {"message": "boom"})
    assert ctx.get_status() == "partial_error"

    ctx.set_error({"message": "global boom"})
    assert ctx.get_status() == "error"


def test_resource_access_error_exposes_context_attributes():
    """ResourceAccessError exposes its context as direct attributes (TS parity)."""
    ctx = AccessContext()
    ctx.set_token("https://api.example.com", _token())

    with pytest.raises(ResourceAccessError) as exc_info:
        ctx.access("https://missing.example.com")

    err = exc_info.value
    assert err.resource == "https://missing.example.com"
    assert err.error_type == "missing_token"
    assert set(err.available_resources) == {"https://api.example.com"}
    assert err.error_details is None


def test_resource_access_error_attributes_for_resource_error():
    ctx = AccessContext()
    detail = {"message": "denied by AS", "code": "access_denied"}
    ctx.set_resource_error("https://api.example.com", detail)

    with pytest.raises(ResourceAccessError) as exc_info:
        ctx.access("https://api.example.com")

    err = exc_info.value
    assert err.resource == "https://api.example.com"
    assert err.error_type == "resource_error"
    assert err.error_details == detail


def test_get_errors_always_returns_a_dict():
    ctx = AccessContext()
    assert ctx.get_errors() == {"resources": {}, "error": None}

    ctx.set_resource_error("https://api.example.com", {"message": "boom"})
    ctx.set_error({"message": "global"})
    snapshot = ctx.get_errors()
    assert snapshot["error"] == {"message": "global"}
    assert snapshot["resources"]["https://api.example.com"] == {"message": "boom"}
