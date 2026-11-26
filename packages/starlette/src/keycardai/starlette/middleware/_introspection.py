from __future__ import annotations

import logging
from urllib.parse import urlsplit

from anyio import fail_after

from keycardai.oauth import AsyncClient, ClientConfig
from keycardai.oauth.client_auth import (
    ClientAuthenticator,
    require_client_authenticator,
)
from keycardai.oauth.exceptions import (
    ConfigError,
    OAuthError,
    OAuthHttpError,
    OAuthProtocolError,
)
from keycardai.oauth.server.client_factory import ClientFactory

logger = logging.getLogger(__name__)


def validate_credential(
    credential: object, *, multi_zone: bool, timeout: float
) -> ClientAuthenticator:
    if credential is None:
        raise ConfigError("Token introspection requires a credential")
    if multi_zone and not getattr(credential, "zone_issuers", None):
        raise ConfigError("Multi-zone token introspection requires zone-keyed credentials")
    if not 0 < timeout < float("inf"):
        raise ConfigError("The introspection timeout must be positive and finite")
    return require_client_authenticator(credential)


class IntrospectionUnavailable(OAuthError):
    pass


class TokenIntrospector:
    def __init__(
        self, credential: ClientAuthenticator, factory: ClientFactory, timeout: float
    ):
        self._credential = credential
        self._factory = factory
        self._timeout = timeout
        self._clients: dict[str, AsyncClient] = {}
        self._logged_errors: set[tuple[str, int | None, str | None]] = set()

    async def is_active(self, token: str, issuer: str | None) -> bool:
        try:
            if issuer is None:
                raise ConfigError("The verified token has no issuer")
            if urlsplit(issuer).scheme != "https":
                raise ConfigError("Token introspection requires an HTTPS issuer")
            client = self._clients.get(issuer)
            if client is None:
                client = self._factory.create_async_client(
                    issuer,
                    config=ClientConfig(
                        enable_metadata_discovery=True,
                        auto_register_client=False,
                        timeout=self._timeout,
                    ),
                )
                self._clients[issuer] = client
            with fail_after(self._timeout):
                result = await client.introspect_token(
                    token, credential=self._credential, timeout=self._timeout
                )
            return result.active
        except Exception as exc:
            status = (
                exc.status_code
                if isinstance(exc, (OAuthHttpError, OAuthProtocolError))
                else None
            )
            cause = type(exc.__cause__).__name__ if exc.__cause__ else None
            if isinstance(exc, OAuthProtocolError) and exc.error in {
                "invalid_client", "invalid_request", "invalid_response", "server_error"
            }:
                cause = exc.error
            key = (type(exc).__name__, status, cause)
            if key not in self._logged_errors:
                self._logged_errors.add(key)
                logger.warning(
                    "Token introspection failed: %s (status=%s, cause=%s)", *key
                )
            raise IntrospectionUnavailable("Token introspection unavailable", exc) from exc
