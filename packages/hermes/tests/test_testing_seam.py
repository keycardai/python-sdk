from keycardai.hermes import get_access_context
from keycardai.hermes.testing import mock_access_context

from .conftest import RESOURCE


def test_mock_access_context_serves_tokens_and_errors() -> None:
    with mock_access_context(resource_tokens={RESOURCE: "tok"}):
        assert get_access_context().access(RESOURCE).access_token == "tok"
    with mock_access_context(access_token="any"):
        assert get_access_context().access("https://whatever").access_token == "any"
    with mock_access_context(error_message="no identity") as access:
        assert access.has_error()
