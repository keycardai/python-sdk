from contextlib import contextmanager

from keycardai.oauth.types.models import TokenResponse

from ..provider import AccessContext, override_access_context


class _MockAccessContext(AccessContext):
    """AccessContext preloaded for tests.

    Behaves like a real AccessContext, with one addition: when constructed
    with a ``default_token``, :meth:`access` returns that token for any
    resource that has no explicit token or error. When a resource lookup
    fails, the miss is recorded as a resource error so ``has_errors()``
    reflects it, matching how grant resolution records failures.
    """

    def __init__(self, default_token: str | None = None):
        super().__init__()
        self._default_token = default_token

    def access(self, resource: str) -> TokenResponse:
        if (
            self._default_token is not None
            and not self.has_errors()
            and resource not in self._access_tokens
        ):
            return TokenResponse(
                access_token=self._default_token,
                token_type="Bearer",
            )
        try:
            return super().access(resource)
        except Exception:
            # Recording the miss on a read path is test-double behavior only:
            # it keeps has_errors() observable after a failed lookup, which
            # tests written against the original mock rely on.
            if not self.has_error() and not self.has_resource_error(resource):
                self.set_resource_error(
                    resource, {"message": f"Resource not granted: {resource}"}
                )
            raise


@contextmanager
def mock_access_context(
    access_token: str = "test_access_token",
    resource_tokens: dict[str, str] | None = None,
    has_errors: bool = False,
    error_message: str = "Mock authentication error",
):
    """Mock the authentication system for testing.

    Builds an :class:`AccessContext` from the given tokens or error state and
    installs it through the public :func:`override_access_context` seam, so
    every grant resolution (injected-parameter or decorator form) yields it
    without touching real token acquisition or exchange. No module internals
    are patched.

    Args:
        access_token: Default access token to return for any resource (str)
        resource_tokens: Dict mapping resource URLs to specific access tokens (dict[str, str])
        has_errors: Whether the access context should report errors (bool)
        error_message: Error message to return when has_errors=True (str)

    Note:
        The bare ``access_token`` form answers for **any** resource, including
        ones the tool never granted, so it cannot catch a mistyped resource URL
        in an ``access(...)`` call; in production that raises
        :class:`ResourceAccessError`. Use ``resource_tokens={...}`` when the
        test should enforce which resources the tool reads.

    Examples:
        # 1. Default - always returns access token
        with mock_access_context():
            # Will return "test_access_token" for any resource

        # 2. Returns access token for provided resource
        with mock_access_context(access_token="my_token"):
            # Will return "my_token" for any resource

        # 3. Return access token for provided dict of resources
        with mock_access_context(resource_tokens={
            "https://api.example.com": "token_123",
            "https://api.other.com": "token_456"
        }):
            # Will return specific tokens for each resource
            # Any resource not in the dict raises ResourceAccessError and
            # records a "Resource not granted" error on the context

        # 4. Returns error set to true and error message
        with mock_access_context(has_errors=True, error_message="Auth failed"):
            # Will report errors with the specified message
    """
    access_context = _MockAccessContext(
        default_token=None if resource_tokens is not None else access_token
    )

    if has_errors:
        access_context.set_error({"message": error_message})
    elif resource_tokens is not None:
        for resource, token in resource_tokens.items():
            access_context.set_token(
                resource,
                TokenResponse(access_token=token, token_type="Bearer"),
            )

    with override_access_context(access_context):
        yield access_context
