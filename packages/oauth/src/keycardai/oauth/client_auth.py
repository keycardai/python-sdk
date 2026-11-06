"""Client authentication, an optional capability of a credential.

A credential that implements ClientAuthenticator returns the headers and
form fields that authenticate the client on one request. Refresh with a
credential and the device authorization grant (RFC 8628) use it.

The capability is separate from the credential protocol, so a credential
implemented outside the SDK keeps working without it. Passing a credential
without it to an operation that needs it raises ConfigError before any
request is sent.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .exceptions import ConfigError


@dataclass(frozen=True)
class ClientAuthentication:
    """Headers and form fields that authenticate the client on one request.

    Holds only client authentication. It never includes a grant parameter
    such as ``grant_type`` or ``subject_token_type``.
    """

    headers: dict[str, str] = field(default_factory=dict)
    form_fields: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class ClientAuthenticator(Protocol):
    """A credential that can authenticate the client on any endpoint."""

    async def client_authentication(
        self, *, issuer: str, endpoint: str
    ) -> ClientAuthentication:
        """Return the client authentication for one request.

        Args:
            issuer: The zone's issuer. A multi-zone credential selects its
                zone by it and fails for an unknown zone.
            endpoint: The URL the request goes to. An assertion-based
                credential signs it as the assertion's ``aud``.

        Each call returns new values, so every request gets its own
        assertion.
        """
        ...


def require_client_authenticator(credential: object) -> ClientAuthenticator:
    """Return ``credential`` as a ClientAuthenticator.

    Raises:
        ConfigError: The credential does not provide client authentication.
    """
    if not isinstance(credential, ClientAuthenticator):
        raise ConfigError(
            f"{type(credential).__name__} does not provide client authentication"
        )
    return credential
