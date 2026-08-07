"""Install a preloaded AccessContext for the duration of a test."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from keycardai.oauth.server.access_context import AccessContext
from keycardai.oauth.types.models import TokenResponse

from ..middleware import _current_access


@contextmanager
def override_access_context(access_context: AccessContext) -> Iterator[AccessContext]:
    """Serve `access_context` to tools for the duration of the block.

    The full-control seam: build the AccessContext yourself (including partial
    failures) and hand it over. `mock_access_context` covers the common cases.
    """
    token = _current_access.set(access_context)
    try:
        yield access_context
    finally:
        _current_access.reset(token)


@contextmanager
def mock_access_context(
    access_token: str | None = None,
    resource_tokens: dict[str, str] | None = None,
    resource_errors: dict[str, str] | None = None,
    error_message: str | None = None,
) -> Iterator[AccessContext]:
    """Serve a synthetic AccessContext to tools, with no exchange performed.

    Args:
        access_token: Served for every resource. Convenient, but it cannot
            catch a mistyped resource URL in an `access(...)` call, since every
            lookup succeeds. Prefer `resource_tokens` when the test should
            assert which resource a tool reads.
        resource_tokens: Per-resource tokens, keyed by resource URL.
        resource_errors: Per-resource failures, keyed by resource URL, as a
            grant failure would record them.
        error_message: A global failure (no identity, unreachable zone). Takes
            precedence: no resource tokens are served.
    """
    context = _AnyResourceAccessContext(access_token)

    if error_message is not None:
        context.set_error({"message": error_message, "code": "mock_error"})
    else:
        for resource, token in (resource_tokens or {}).items():
            context.set_token(
                resource, TokenResponse(access_token=token, token_type="Bearer")
            )
        for resource, message in (resource_errors or {}).items():
            context.set_resource_error(
                resource, {"message": message, "code": "mock_resource_error"}
            )

    with override_access_context(context):
        yield context


class _AnyResourceAccessContext(AccessContext):
    """AccessContext that can serve one token for any resource.

    Only used when `mock_access_context(access_token=...)` is given; with
    `resource_tokens` the base class behavior applies unchanged.
    """

    def __init__(self, default_token: str | None = None) -> None:
        super().__init__()
        self._default_token = default_token

    def access(self, resource: str) -> TokenResponse:
        if (
            self._default_token is not None
            and not self.has_errors()
            and resource not in self.get_successful_resources()
        ):
            return TokenResponse(access_token=self._default_token, token_type="Bearer")
        return super().access(resource)
