from __future__ import annotations

import pytest
from test_middleware import (
    PROMPT,
    StubExchangeClient,
    build_agent,
    last_tool_message,
)

from keycardai.langchain import Access, KeycardIdentity


def test_factories_build_the_expected_identities() -> None:
    assert Access.as_self() == KeycardIdentity(as_self=True)
    assert Access.on_behalf_of("caller-token") == KeycardIdentity(
        subject_token="caller-token"
    )
    assert Access.impersonate("user@example.com") == KeycardIdentity(
        user_identifier="user@example.com"
    )


def test_access_namespace_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        Access()


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_on_behalf_of_rejects_empty_subject_tokens(value: str) -> None:
    with pytest.raises(ValueError, match="non-empty subject token"):
        Access.on_behalf_of(value)


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_impersonate_rejects_empty_user_identifiers(value: str) -> None:
    with pytest.raises(ValueError, match="non-empty user identifier"):
        Access.impersonate(value)


def test_as_self_uses_client_credentials_without_exchange() -> None:
    stub = StubExchangeClient()
    result = build_agent(stub).invoke(PROMPT, context=Access.as_self())

    assert "TOKEN: self-token-for-https://api.example.test" in last_tool_message(
        result
    ).content
    assert not stub.exchange_calls
    assert stub.self_calls == [{"resource": "https://api.example.test"}]


def test_on_behalf_of_exchanges_the_subject_token() -> None:
    stub = StubExchangeClient()
    result = build_agent(stub).invoke(
        PROMPT, context=Access.on_behalf_of("caller-token")
    )

    assert "TOKEN: obo-token-for-https://api.example.test" in last_tool_message(
        result
    ).content
    assert stub.exchange_calls[0].subject_token == "caller-token"


def test_impersonate_uses_substitute_user_without_exchange() -> None:
    stub = StubExchangeClient()
    result = build_agent(stub).invoke(
        PROMPT, context=Access.impersonate("user@example.com")
    )

    assert "TOKEN: impersonated-user@example.com" in last_tool_message(result).content
    assert not stub.exchange_calls
    assert stub.impersonate_calls == [
        {"user": "user@example.com", "resource": "https://api.example.test"}
    ]
