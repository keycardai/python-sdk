"""Factories for the identity a run acts under."""

from __future__ import annotations

from .middleware import KeycardIdentity


class Access:
    """Namespace of factories for the identity a run acts under.

    Each classmethod builds the KeycardIdentity for one access pattern, so a
    call site names the pattern instead of setting a field:

        agent.invoke({"messages": [...]}, context=Access.as_self())

    The agent's context_schema stays KeycardIdentity; these factories only
    construct it.
    """

    def __init__(self) -> None:
        raise TypeError(
            "Access is a namespace of factories, not a type. Call "
            "Access.as_self(), Access.on_behalf_of(subject_token), or "
            "Access.impersonate(user_identifier)."
        )

    @classmethod
    def as_self(cls) -> KeycardIdentity:
        """The agent acts as its own application: client credentials, no user."""
        return KeycardIdentity(as_self=True)

    @classmethod
    def on_behalf_of(cls, subject_token: str) -> KeycardIdentity:
        """The agent acts for the caller, exchanging the caller's token (RFC 8693)."""
        if not subject_token or not subject_token.strip():
            raise ValueError("Access.on_behalf_of requires a non-empty subject token")
        return KeycardIdentity(subject_token=subject_token)

    @classmethod
    def impersonate(cls, user_identifier: str) -> KeycardIdentity:
        """The agent acts as a named user, authenticated by its own credential."""
        if not user_identifier or not user_identifier.strip():
            raise ValueError("Access.impersonate requires a non-empty user identifier")
        return KeycardIdentity(user_identifier=user_identifier)
