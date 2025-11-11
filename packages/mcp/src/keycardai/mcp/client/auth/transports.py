"""HTTP transport adapters for authentication strategies."""

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from httpx import Auth, Request, Response

from ..logging_config import get_logger

if TYPE_CHECKING:
    from .strategies.oauth import AuthStrategy

logger = get_logger(__name__)


class HttpxAuth(Auth):
    """
    Adapts AuthStrategy to httpx.Auth interface.

    This is a thin adapter that translates between httpx's auth flow
    and our transport-agnostic AuthStrategy protocol.

    All business logic (OAuth discovery, token management, etc.) is
    delegated to the AuthStrategy implementation.

    Note: Strategy already has storage and coordinator from its constructor.
    """

    def __init__(self, strategy: "AuthStrategy"):
        """
        Initialize httpx auth adapter.

        Args:
            strategy: Authentication strategy (already initialized with storage/coordinator)
        """
        self.strategy = strategy
        self.resource_url: str | None = None

    async def async_auth_flow(self, request: Request) -> AsyncGenerator[Request, Response]:
        """
        httpx auth flow - delegates to AuthStrategy.

        This method:
        1. Adds auth metadata to the request (if available)
        2. Sends the request
        3. Handles auth challenges (401/403) via the strategy
        4. Retries if the strategy handled the challenge
        """
        # Build resource URL from request
        self.resource_url = (
            f"{request.url.scheme}://"
            f"{request.url.netloc.decode('utf-8')}/"
            f"{request.url.path.lstrip('/').rstrip('/')}"
        )

        # Get auth metadata from strategy
        try:
            auth_metadata = await self.strategy.get_auth_metadata()
            headers = auth_metadata.get("headers", {})
            request.headers.update(headers)
        except Exception as e:
            logger.error(f"Error getting auth metadata: {e}")

        # Send request
        response = yield request

        # Handle auth challenges
        if response.status_code in (401, 403):
            try:
                should_retry = await self.strategy.handle_challenge(
                    challenge=response,
                    resource_url=self.resource_url
                )

                if should_retry:
                    # Strategy initiated auth flow - retry with new auth metadata
                    # Get updated auth metadata (e.g., newly acquired bearer token)
                    try:
                        auth_metadata = await self.strategy.get_auth_metadata()
                        headers = auth_metadata.get("headers", {})
                        request.headers.update(headers)
                        logger.info("Auth challenge handled, retrying with new credentials")
                    except Exception as e:
                        logger.error(f"Error getting updated auth metadata: {e}")

                    yield request
                else:
                    # Strategy couldn't handle challenge
                    logger.debug(f"Auth challenge not handled for status {response.status_code}")

            except Exception as e:
                logger.error(f"Error handling auth challenge: {e}", exc_info=True)

