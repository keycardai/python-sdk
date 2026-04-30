"""Server-to-server delegation client using OAuth token exchange.

This module provides clients for agent services to delegate tasks to other
agent services while maintaining the user context.
"""

import json
import logging
import uuid
from typing import Any

import httpx
from a2a.utils import constants

from keycardai.oauth import AsyncClient as AsyncOAuthClient
from keycardai.oauth import Client as SyncOAuthClient
from keycardai.oauth.http.auth import BasicAuth
from keycardai.oauth.types.models import TokenExchangeRequest
from keycardai.oauth.types.oauth import TokenType

from ..config import AgentServiceConfig

logger = logging.getLogger(__name__)


def _build_jsonrpc_send_message(task: dict[str, Any] | str) -> dict[str, Any]:
    """Wrap a task in an A2A 1.x JSONRPC ``SendMessage`` envelope.

    ``task`` may be a plain string, a dict carrying a ``"task"`` string under
    that key (legacy shape preserved for the CrewAI integration), or any
    other dict (serialized to JSON for the message text).

    The shape mirrors ``a2a.types.SendMessageRequest`` after JSON-marshalling
    via ``google.protobuf.json_format``: a ``messageId`` (required by the
    dispatcher), an enum-string ``role``, and ``parts`` carrying the text.
    """
    if isinstance(task, str):
        text = task
    elif isinstance(task, dict):
        if isinstance(task.get("task"), str):
            text = task["task"]
        else:
            text = json.dumps(task)
    else:
        raise ValueError(f"Invalid task type: {type(task)}")

    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "ROLE_USER",
                "parts": [{"text": text}],
            },
        },
    }


def _unwrap_jsonrpc_response(response_body: dict[str, Any]) -> dict[str, Any]:
    """Unwrap an A2A 1.x JSONRPC ``SendMessageResponse`` into the
    ``{result, delegation_chain}`` shape consumed by the CrewAI delegation
    tool.

    ``SendMessageResponse`` is a oneof of ``message`` or ``task``. If the
    remote executor enqueued a ``Message`` (the common case for synchronous
    crews), the text is at ``result.message.parts[].text``. If it produced a
    ``Task``, we fall back to JSON-stringifying the task; callers wanting the
    full Task lifecycle should reach for ``a2a.client.create_client``.

    ``delegation_chain`` is returned empty: the legacy chain reconstruction
    read from ``request.state.keycardai_auth_info``, which never carried the
    claim, so it was always single-hop. Callers needing multi-hop tracking
    should parse JWT claims directly.

    Raises:
        ValueError: if the response carries a JSONRPC ``error`` member.
    """
    if "error" in response_body:
        err = response_body["error"] or {}
        raise ValueError(
            f"JSONRPC error from agent service: "
            f"{err.get('code')} {err.get('message')}"
        )
    result = response_body.get("result")
    if result is None:
        return {"result": "", "delegation_chain": []}
    if isinstance(result, dict):
        message = result.get("message")
        if isinstance(message, dict):
            parts = message.get("parts")
            if isinstance(parts, list):
                text_parts = [
                    p.get("text", "")
                    for p in parts
                    if isinstance(p, dict) and "text" in p
                ]
                if text_parts:
                    return {
                        "result": "\n".join(text_parts),
                        "delegation_chain": [],
                    }
    if isinstance(result, str):
        return {"result": result, "delegation_chain": []}
    return {"result": json.dumps(result), "delegation_chain": []}


class DelegationClient:
    """Async client for server-to-server delegation using OAuth token exchange.

    Enables an agent service to:
    1. Discover other agent services (fetch agent cards)
    2. Obtain delegation tokens (RFC 8693 token exchange)
    3. Invoke other agent services with proper authentication

    This implements the A2A (agent-to-agent) communication pattern where
    services can delegate tasks to other services while maintaining the
    full delegation chain for audit purposes.

    Args:
        service_config: Configuration of the calling service

    Example:
        >>> from keycardai.a2a import AgentServiceConfig
        >>> from keycardai.a2a.server import DelegationClient
        >>> 
        >>> config = AgentServiceConfig(...)
        >>> client = DelegationClient(config)
        >>>
        >>> # Discover service capabilities
        >>> card = await client.discover_service("https://slack-poster.example.com")
        >>> print(card["capabilities"])
        >>>
        >>> # Get token for calling that service
        >>> token = await client.get_delegation_token(
        ...     "https://slack-poster.example.com",
        ...     subject_token="current_user_token"
        ... )
        >>>
        >>> # Invoke the service
        >>> result = await client.invoke_service(
        ...     "https://slack-poster.example.com",
        ...     {"task": "Post message to #engineering"},
        ...     token
        ... )
    """

    def __init__(self, service_config: AgentServiceConfig):
        """Initialize delegation client with service configuration.

        Args:
            service_config: Configuration of the calling service
        """
        self.config = service_config

        # Initialize OAuth client for token exchange
        # Use configured authorization server URL (defaults to zone URL)
        oauth_base_url = service_config.auth_server_url
        self.oauth_client = AsyncOAuthClient(
            oauth_base_url,
            auth=BasicAuth(service_config.client_id, service_config.client_secret),
        )

        # HTTP client for service invocation
        self.http_client = httpx.AsyncClient(timeout=30.0)

    async def discover_service(self, service_url: str) -> dict[str, Any]:
        """Fetch agent card from remote service.

        Fetches the agent card from the well-known endpoint to discover
        service capabilities, endpoints, and authentication requirements.

        Args:
            service_url: Base URL of the target service

        Returns:
            Agent card dictionary with service metadata

        Raises:
            httpx.HTTPStatusError: If agent card fetch fails
            ValueError: If agent card format is invalid

        Example:
            >>> card = await client.discover_service("https://slack-poster.example.com")
            >>> print(card["capabilities"])
            ['slack_posting', 'message_formatting']
        """
        # Ensure URL doesn't have trailing slash
        service_url = service_url.rstrip("/")

        # Fetch agent card from well-known endpoint
        agent_card_url = f"{service_url}/.well-known/agent-card.json"

        try:
            response = await self.http_client.get(agent_card_url)
            response.raise_for_status()

            card = response.json()

            # The 1.x card emitter (a2a-sdk) populates a "name" field on every
            # valid card. Trust it for the rest; transport / auth / interface
            # specifics are surfaced via supported_interfaces and the
            # OAuth metadata routes, not the card validator.
            if not card.get("name"):
                raise ValueError("Invalid agent card: missing required field 'name'")

            logger.info(f"Discovered service: {card.get('name')} at {service_url}")
            return card

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to fetch agent card from {agent_card_url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error discovering service at {service_url}: {e}")
            raise

    async def get_delegation_token(
        self,
        target_service_url: str,
        subject_token: str | None = None,
    ) -> str:
        """Get OAuth token to call target service using RFC 8693 token exchange.

        Exchanges current token (or uses service credentials) for a token
        scoped to the target service. The delegation chain is preserved
        in the new token.

        Args:
            target_service_url: Base URL of the target service
            subject_token: Optional current token to exchange (for user context)

        Returns:
            Access token for calling the target service

        Raises:
            OAuthHttpError: If token exchange fails
            OAuthProtocolError: If response is invalid

        Example:
            >>> token = await client.get_delegation_token(
            ...     "https://slack-poster.example.com",
            ...     subject_token="user_access_token"
            ... )
        """
        # Ensure URL doesn't have trailing slash
        target_service_url = target_service_url.rstrip("/")

        try:
            if subject_token:
                # Token exchange: user token → service token
                # This preserves the user context in the delegation chain
                request = TokenExchangeRequest(
                    grant_type="urn:ietf:params:oauth:grant-type:token-exchange",
                    subject_token=subject_token,
                    subject_token_type=TokenType.ACCESS_TOKEN,
                    resource=target_service_url,
                    audience=target_service_url,
                )
            else:
                # Client credentials: service → service
                # Direct service-to-service call without user context
                request = TokenExchangeRequest(
                    grant_type="client_credentials",
                    resource=target_service_url,
                    audience=target_service_url,
                )

            # Perform token exchange
            response = await self.oauth_client.exchange_token(request)

            logger.info(
                f"Obtained delegation token for {target_service_url} "
                f"(expires_in={response.expires_in})"
            )

            return response.access_token

        except Exception as e:
            logger.error(f"Token exchange failed for {target_service_url}: {e}")
            raise

    async def invoke_service(
        self,
        service_url: str,
        task: dict[str, Any] | str,
        token: str | None = None,
        subject_token: str | None = None,
    ) -> dict[str, Any]:
        """Call another agent service over A2A JSONRPC with bearer auth.

        Sends a ``SendMessage`` JSONRPC request to ``${service_url}/a2a/jsonrpc``
        and returns ``{"result": <text>, "delegation_chain": []}`` for
        compatibility with the legacy invocation surface. If you need the
        full A2A protocol surface (Task lifecycle, streaming, status
        updates), use ``a2a.client.create_client`` directly.

        Args:
            service_url: Base URL of the target service
            task: Task description or parameters
            token: Optional pre-obtained access token
            subject_token: Optional token for exchange if token not provided

        Returns:
            Dict with ``result`` (str) and ``delegation_chain`` (list).

        Raises:
            httpx.HTTPStatusError: If the JSONRPC request fails
            ValueError: If the response carries a JSONRPC error

        Example:
            >>> result = await client.invoke_service(
            ...     "https://slack-poster.example.com",
            ...     {"task": "Post to #engineering", "message": "Deploy complete"},
            ...     token="access_token_123"
            ... )
            >>> print(result["result"])
        """
        service_url = service_url.rstrip("/")

        if not token:
            token = await self.get_delegation_token(service_url, subject_token)

        jsonrpc_url = f"{service_url}/a2a/jsonrpc"
        envelope = _build_jsonrpc_send_message(task)

        try:
            response = await self.http_client.post(
                jsonrpc_url,
                json=envelope,
                headers={
                    "Authorization": f"Bearer {token}",
                    constants.VERSION_HEADER: constants.PROTOCOL_VERSION_1_0,
                },
            )
            response.raise_for_status()
            unwrapped = _unwrap_jsonrpc_response(response.json())
            logger.info(f"Service invocation successful: {service_url}")
            return unwrapped

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Service invocation failed for {service_url}: "
                f"status={e.response.status_code}, body={e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(f"Error invoking service {service_url}: {e}")
            raise

    async def close(self) -> None:
        """Close HTTP client connections.

        Should be called when the client is no longer needed.
        """
        await self.http_client.aclose()

    async def __aenter__(self) -> "DelegationClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()


class DelegationClientSync:
    """Synchronous client for server-to-server delegation using OAuth token exchange.

    Enables an agent service to delegate tasks to other agent services using blocking I/O.
    Safe to use in environments with existing event loops (like uvloop).

    This implements the A2A (agent-to-agent) communication pattern where
    services can delegate tasks to other services while maintaining the
    full delegation chain for audit purposes.

    Args:
        service_config: Configuration of the calling service

    Example:
        >>> from keycardai.a2a import AgentServiceConfig
        >>> from keycardai.a2a.server import DelegationClientSync
        >>> 
        >>> config = AgentServiceConfig(...)
        >>> client = DelegationClientSync(config)
        >>>
        >>> # Discover service capabilities
        >>> card = client.discover_service("https://slack-poster.example.com")
        >>> print(card["capabilities"])
        >>>
        >>> # Invoke the service
        >>> result = client.invoke_service(
        ...     "https://slack-poster.example.com",
        ...     {"task": "Post message to #engineering"}
        ... )
    """

    def __init__(self, service_config: AgentServiceConfig):
        """Initialize synchronous delegation client with service configuration.

        Args:
            service_config: Configuration of the calling service
        """
        self.config = service_config

        # Initialize OAuth client for token exchange
        # Use configured authorization server URL (defaults to zone URL)
        oauth_base_url = service_config.auth_server_url
        self.oauth_client = SyncOAuthClient(
            oauth_base_url,
            auth=BasicAuth(service_config.client_id, service_config.client_secret),
        )

        # HTTP client for service invocation
        self.http_client = httpx.Client(timeout=30.0)

    def discover_service(self, service_url: str) -> dict[str, Any]:
        """Fetch agent card from remote service.

        Fetches the agent card from the well-known endpoint to discover
        service capabilities, endpoints, and authentication requirements.

        Args:
            service_url: Base URL of the target service

        Returns:
            Agent card dictionary with service metadata

        Raises:
            httpx.HTTPStatusError: If agent card fetch fails
            ValueError: If agent card format is invalid

        Example:
            >>> card = client.discover_service("https://slack-poster.example.com")
            >>> print(card["capabilities"])
            ['slack_posting', 'message_formatting']
        """
        # Ensure URL doesn't have trailing slash
        service_url = service_url.rstrip("/")

        # Fetch agent card from well-known endpoint
        agent_card_url = f"{service_url}/.well-known/agent-card.json"

        try:
            response = self.http_client.get(agent_card_url)
            response.raise_for_status()

            card = response.json()

            # The 1.x card emitter (a2a-sdk) populates a "name" field on every
            # valid card. Trust it for the rest; transport / auth / interface
            # specifics are surfaced via supported_interfaces and the
            # OAuth metadata routes, not the card validator.
            if not card.get("name"):
                raise ValueError("Invalid agent card: missing required field 'name'")

            logger.info(f"Discovered service: {card.get('name')} at {service_url}")
            return card

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to fetch agent card from {agent_card_url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error discovering service at {service_url}: {e}")
            raise

    def get_delegation_token(
        self,
        target_service_url: str,
        subject_token: str | None = None,
    ) -> str:
        """Get OAuth token to call target service using RFC 8693 token exchange.

        Exchanges current token (or uses service credentials) for a token
        scoped to the target service. The delegation chain is preserved
        in the new token.

        Args:
            target_service_url: Base URL of the target service
            subject_token: Optional current token to exchange (for user context)

        Returns:
            Access token for calling the target service

        Raises:
            OAuthHttpError: If token exchange fails
            OAuthProtocolError: If response is invalid

        Example:
            >>> token = client.get_delegation_token(
            ...     "https://slack-poster.example.com",
            ...     subject_token="user_access_token"
            ... )
        """
        # Ensure URL doesn't have trailing slash
        target_service_url = target_service_url.rstrip("/")

        try:
            if subject_token:
                # Token exchange: user token → service token
                # This preserves the user context in the delegation chain
                request = TokenExchangeRequest(
                    grant_type="urn:ietf:params:oauth:grant-type:token-exchange",
                    subject_token=subject_token,
                    subject_token_type=TokenType.ACCESS_TOKEN,
                    resource=target_service_url,
                    audience=target_service_url,
                )
            else:
                # Client credentials: service → service
                # Direct service-to-service call without user context
                request = TokenExchangeRequest(
                    grant_type="client_credentials",
                    resource=target_service_url,
                    audience=target_service_url,
                )

            # Perform token exchange
            response = self.oauth_client.exchange_token(request)

            logger.info(
                f"Obtained delegation token for {target_service_url} "
                f"(expires_in={response.expires_in})"
            )

            return response.access_token

        except Exception as e:
            logger.error(f"Token exchange failed for {target_service_url}: {e}")
            raise

    def invoke_service(
        self,
        service_url: str,
        task: dict[str, Any] | str,
        token: str | None = None,
        subject_token: str | None = None,
    ) -> dict[str, Any]:
        """Call another agent service over A2A JSONRPC with bearer auth.

        Sends a ``SendMessage`` JSONRPC request to ``${service_url}/a2a/jsonrpc``
        and returns ``{"result": <text>, "delegation_chain": []}`` for
        compatibility with the legacy invocation surface.

        Args:
            service_url: Base URL of the target service
            task: Task description or parameters
            token: Optional pre-obtained access token
            subject_token: Optional token for exchange if token not provided

        Returns:
            Dict with ``result`` (str) and ``delegation_chain`` (list).

        Raises:
            httpx.HTTPStatusError: If the JSONRPC request fails
            ValueError: If the response carries a JSONRPC error
        """
        service_url = service_url.rstrip("/")

        if not token:
            token = self.get_delegation_token(service_url, subject_token)

        jsonrpc_url = f"{service_url}/a2a/jsonrpc"
        envelope = _build_jsonrpc_send_message(task)

        try:
            response = self.http_client.post(
                jsonrpc_url,
                json=envelope,
                headers={
                    "Authorization": f"Bearer {token}",
                    constants.VERSION_HEADER: constants.PROTOCOL_VERSION_1_0,
                },
            )
            response.raise_for_status()
            unwrapped = _unwrap_jsonrpc_response(response.json())
            logger.info(f"Service invocation successful: {service_url}")
            return unwrapped

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Service invocation failed for {service_url}: "
                f"status={e.response.status_code}, body={e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(f"Error invoking service {service_url}: {e}")
            raise

    def close(self) -> None:
        """Close HTTP client connections.

        Should be called when the client is no longer needed.
        """
        self.http_client.close()

    def __enter__(self) -> "DelegationClientSync":
        """Synchronous context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Synchronous context manager exit."""
        self.close()
