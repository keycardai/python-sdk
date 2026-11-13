"""Client authentication, an optional capability of a credential.

A credential that implements ClientAuthenticator returns the headers and
form fields that authenticate the client on one request. The capability is
separate from ApplicationCredential, so a credential implemented outside the
SDK does not need it.
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
    """A credential that can authenticate the client on any endpoint.

    A credential may also define ``check_client_authentication()``, which
    raises ConfigError when the credential cannot authenticate the client.
    require_client_authenticator calls it.
    """

    async def client_authentication(
        self, *, issuer: str, endpoint: str
    ) -> ClientAuthentication:
        """Return the client authentication for one request.

        Call it once per request. An assertion-based credential signs or
        fetches a new assertion on every call.

        Args:
            issuer: The zone's issuer. A multi-zone credential selects its
                zone by it and fails for an unknown zone.
            endpoint: The URL the request goes to. An assertion-based
                credential signs it as the assertion's ``aud``.
        """
        ...


def require_client_authenticator(credential: object) -> ClientAuthenticator:
    """Return ``credential`` as a ClientAuthenticator.

    Call it before sending any request.

    Raises:
        ConfigError: The credential does not provide client authentication,
            or its ``check_client_authentication()`` raised.
    """
    if not isinstance(credential, ClientAuthenticator):
        raise ConfigError(
            f"{type(credential).__name__} does not provide client authentication"
        )
    check = getattr(credential, "check_client_authentication", None)
    if callable(check):
        check()
    return credential
