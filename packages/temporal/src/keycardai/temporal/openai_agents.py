"""Keycard-minted OpenAI credential for temporalio's OpenAI Agents plugin.

``OpenAIAgentsPlugin`` runs every model call as a Temporal activity of its
own, and that activity asks its ``ModelProvider`` for a model on each call.
Neither ``@grant`` nor :class:`keycardai.temporal.KeycardInterceptor` can
reach that activity: the plugin registers it and builds its OpenAI client
before any consumer activity exists. :class:`KeycardOpenAIProvider` closes
the gap from the provider side. It hands the OpenAI client an async key
callback instead of a key string, and the client awaits that callback before
every request it sends. The callback mints the OpenAI key from a vaulted
Keycard resource with a client-credentials grant and caches it for a short
``refresh`` window, so a key rotated in Keycard is picked up by the next
model call after the window closes, with no worker restart.

Optional dependencies (``openai``, ``agents``) are imported only here, and
only when a provider is built, so ``import keycardai.temporal`` stays clean
without the ``openai-agents`` extra.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from keycardai.oauth import AsyncClient
    from keycardai.oauth.server import ApplicationCredential, ClientSecret

from keycardai.temporal import (
    GrantConfigurationError,
    _raise_for_grant_failure,
    _worker_credential,
)

if TYPE_CHECKING:
    from agents import Model, ModelProvider

    _ProviderBase = ModelProvider
else:
    # Duck-typed at runtime so this module imports without ``agents``; the
    # plugin only ever calls get_model() and aclose().
    _ProviderBase = object

__all__ = ["KeycardOpenAIKey", "KeycardOpenAIProvider"]

_MISSING_EXTRA = (
    "keycardai.temporal.openai_agents needs the OpenAI Agents SDK and the openai "
    "client: install keycardai-temporal[openai-agents] next to "
    "temporalio[openai-agents]."
)


def _openai_deps() -> tuple[Any, Any]:
    """Import the optional dependencies on first use: (OpenAIProvider, AsyncOpenAI)."""
    try:
        from agents.models.openai_provider import OpenAIProvider
        from openai import AsyncOpenAI
    except ImportError as e:
        raise ImportError(_MISSING_EXTRA) from e
    return OpenAIProvider, AsyncOpenAI


class KeycardOpenAIKey:
    """Async callable returning the OpenAI key vaulted behind a Keycard resource.

    Shaped for ``openai.AsyncOpenAI(api_key=...)``, which awaits it before
    each request. The key is minted with a client-credentials grant and
    cached for ``refresh`` (bounded by the grant's ``expires_in`` when the
    zone reports a shorter one); concurrent callers on a cold or expired
    cache share one mint. A permanent grant failure raises the same
    non-retryable ``KeycardAccessDenied`` ``ApplicationError`` the
    interceptor raises, so the plugin's model activity fails the same way a
    ``@grant`` activity would; a transient failure propagates as-is and the
    model activity's retry policy governs it.

    Args:
        zone_url: Keycard zone issuer URL.
        resource: Identifier of the vaulted OpenAI key resource in the zone.
        credential: How this application authenticates to the zone; when
            omitted, discovered from the environment exactly as
            :class:`keycardai.temporal.KeycardInterceptor` does. Must be a
            ``ClientSecret`` (the client-credentials path has no bridge for
            assertion credentials yet).
        refresh: How long a minted key is reused before the next call mints
            again. Default five minutes.

    Raises:
        GrantConfigurationError: the credential could not be discovered, or
            is not a ``ClientSecret``.
    """

    def __init__(
        self,
        zone_url: str,
        resource: str,
        credential: ApplicationCredential | None = None,
        *,
        refresh: timedelta = timedelta(minutes=5),
    ) -> None:
        credential = _worker_credential(credential)
        if not isinstance(credential, ClientSecret):
            raise GrantConfigurationError(
                f"{type(self).__name__} for {resource} mints with client "
                "credentials, which requires a ClientSecret credential; the "
                f"worker has {type(credential).__name__}."
            )
        if refresh <= timedelta(0):
            raise GrantConfigurationError(
                f"{type(self).__name__}: refresh must be positive, got {refresh}."
            )
        self.resource = resource
        self.refresh = refresh
        self._client = AsyncClient(zone_url, auth=credential.get_http_client_auth())
        self._key: str | None = None
        self._fresh_until = 0.0
        self._lock = asyncio.Lock()

    def _cached(self) -> str | None:
        if self._key is not None and time.monotonic() < self._fresh_until:
            return self._key
        return None

    async def __call__(self) -> str:
        key = self._cached()
        if key is not None:
            return key
        async with self._lock:
            key = self._cached()
            if key is not None:
                return key
            try:
                resp = await self._client.client_credentials_grant(
                    resource=self.resource
                )
            except Exception as e:
                _raise_for_grant_failure(self.resource, e)
            window = self.refresh.total_seconds()
            if resp.expires_in is not None:
                window = min(window, float(resp.expires_in))
            self._key = resp.access_token
            self._fresh_until = time.monotonic() + window
            return self._key

    async def aclose(self) -> None:
        """Close the Keycard client; the next call opens a fresh one."""
        self._key = None
        self._fresh_until = 0.0
        await self._client.aclose()


class KeycardOpenAIProvider(_ProviderBase):
    """``agents.ModelProvider`` whose OpenAI key comes from Keycard per call.

    Pass it as ``OpenAIAgentsPlugin(model_provider=...)``. Models come from
    the SDK's own ``OpenAIProvider`` over one ``AsyncOpenAI`` client that
    carries a :class:`KeycardOpenAIKey` in place of a key string, so every
    request the plugin's model activity sends first resolves (and, past the
    ``refresh`` window, re-mints) the key. ``max_retries`` on that client
    is 0, as in the plugin's default provider, so the model activity's
    retry policy is the only retry loop.

    The plugin requires an explicit ``start_to_close_timeout`` or
    ``schedule_to_close_timeout`` in ``ModelActivityParameters`` whenever a
    custom provider is set; it only defaults the timeout for its own.

    Args:
        zone_url, resource, credential, refresh: as :class:`KeycardOpenAIKey`.
        base_url: OpenAI-compatible base URL, when not api.openai.com.
        use_responses: Forwarded to ``OpenAIProvider`` (Responses API vs
            Chat Completions); ``None`` keeps the SDK default.

    Raises:
        GrantConfigurationError: as :class:`KeycardOpenAIKey`.
        ImportError: the ``openai-agents`` extra is not installed.
    """

    def __init__(
        self,
        zone_url: str,
        resource: str,
        credential: ApplicationCredential | None = None,
        *,
        refresh: timedelta = timedelta(minutes=5),
        base_url: str | None = None,
        use_responses: bool | None = None,
    ) -> None:
        OpenAIProvider, AsyncOpenAI = _openai_deps()
        self.api_key = KeycardOpenAIKey(zone_url, resource, credential, refresh=refresh)
        self._openai = AsyncOpenAI(
            api_key=self.api_key, base_url=base_url, max_retries=0
        )
        self._provider = OpenAIProvider(
            openai_client=self._openai, use_responses=use_responses
        )

    def get_model(self, model_name: str | None) -> Model:
        return self._provider.get_model(model_name)

    async def aclose(self) -> None:
        """Release the inner provider, the OpenAI client, and the Keycard client."""
        await self._provider.aclose()
        await self._openai.close()
        await self.api_key.aclose()
