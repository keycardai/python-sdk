"""Tests for keycardai.oauth.types.models."""

from keycardai.oauth.types.models import Endpoints, ResolvedEndpoints


def _resolved(**overrides: str) -> ResolvedEndpoints:
    urls = {
        "token": "https://z.keycard.cloud/oauth/2/token",
        "introspect": "https://z.keycard.cloud/oauth/2/introspect",
        "revoke": "https://z.keycard.cloud/oauth/2/revoke",
        "register": "https://z.keycard.cloud/oauth/2/registration",
        "par": "https://z.keycard.cloud/oauth/2/par",
        "authorize": "https://z.keycard.cloud/oauth/2/authorize",
    }
    urls.update(overrides)
    return ResolvedEndpoints(**urls)


def test_resolved_endpoints_equal_plain_endpoints_with_same_urls():
    resolved = _resolved()
    plain = Endpoints(
        token=resolved.token,
        introspect=resolved.introspect,
        revoke=resolved.revoke,
        register=resolved.register,
        par=resolved.par,
        authorize=resolved.authorize,
    )
    assert resolved == plain
    assert plain == resolved


def test_resolved_endpoints_differ_when_a_url_differs():
    assert _resolved() != _resolved(token="https://other.keycard.cloud/oauth/2/token")
    assert _resolved() != Endpoints(token=_resolved().token)
