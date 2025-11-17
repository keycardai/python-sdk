"""
Auth tool handlers for customizable authentication flow.

Provides base classes and implementations for handling authentication
challenges in different environments (Slack, CLI, web apps, etc.).
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class AuthToolHandler(ABC):
    """
    Base class for custom authentication tool handlers.

    Subclass this to implement custom authentication flows for your
    application (e.g., sending Slack messages, showing web UI, etc.).

    The handler receives auth challenges and is responsible for
    presenting them to the user in the appropriate way.
    """

    @abstractmethod
    async def handle_auth_request(
        self,
        service: str,
        reason: str,
        challenge: dict[str, Any],
    ) -> str:
        """
        Handle an authentication request.

        This method is called when the agent determines that a service
        needs authentication. Implement this to send auth links to users
        via your preferred channel (Slack DM, email, web notification, etc.).

        Args:
            service: Service name requesting auth (e.g., "google-mcp")
            reason: User-friendly explanation from the agent
                   (e.g., "To send messages to Slack channels")
            challenge: Full challenge dict containing:
                - authorization_url: URL for user to authorize
                - server: Server name
                - Other challenge-specific fields

        Returns:
            Simple status message for the agent (e.g., "Auth flow initiated").
            This is NOT shown to the end user - it's just for the agent
            to know the auth process started.

        Example:
            >>> async def handle_auth_request(self, service, reason, challenge):
            ...     url = challenge["authorization_url"]
            ...     await send_email(user, f"Click here: {url}")
            ...     return "Authorization email sent"
        """
        pass


class DefaultAuthToolHandler(AuthToolHandler):
    """
    Default handler that returns auth info for the agent to display.

    This is the fallback when no custom handler is provided. It returns
    a formatted message that the agent can show to the user.

    Note: This is less ideal than a custom handler because it requires
    the agent to relay the message, which may not work well in all UIs.
    """

    async def handle_auth_request(
        self,
        service: str,
        reason: str,
        challenge: dict[str, Any],
    ) -> str:
        """Return formatted auth message for agent to display."""
        auth_url = challenge.get("authorization_url", "")

        message_parts = [
            f"🔐 **Authorization Required for {service}**\n",
            f"{reason}\n" if reason else "",
            f"**Click to authorize:** {auth_url}\n",
            "I'll automatically continue once you authorize!",
        ]

        return "\n".join(part for part in message_parts if part)


class SlackAuthToolHandler(AuthToolHandler):
    """
    Slack-specific handler that sends auth links via Slack messages.

    This handler sends authentication links directly to the user
    in their Slack thread, bypassing the agent message flow.

    Example:
        >>> from slack_sdk.web.async_client import AsyncWebClient
        >>> handler = SlackAuthToolHandler(
        ...     slack_client=client,
        ...     channel_id="C123456",
        ...     thread_ts="1234567890.123456"
        ... )
    """

    def __init__(
        self,
        slack_client: Any,
        channel_id: str,
        thread_ts: str,
        team_id: str | None = None,
        user_id: str | None = None,
    ):
        """
        Initialize Slack auth handler.

        Args:
            slack_client: AsyncWebClient from slack_sdk
            channel_id: Slack channel ID for the conversation
            thread_ts: Thread timestamp for threading messages
            team_id: Optional team ID for enterprise grid
            user_id: Optional user ID for DMs
        """
        self.slack_client = slack_client
        self.channel_id = channel_id
        self.thread_ts = thread_ts
        self.team_id = team_id
        self.user_id = user_id

    async def handle_auth_request(
        self,
        service: str,
        reason: str,
        challenge: dict[str, Any],
    ) -> str:
        """Send auth link via Slack message."""
        auth_url = challenge.get("authorization_url", "")

        # Build message text
        message_parts = [
            "🔐 **Authorization Required**",
            f"\n**Service:** {service}",
        ]

        if reason:
            message_parts.append(f"\n**Reason:** {reason}")

        message_parts.append(f"\n\n<{auth_url}|Click here to authorize>")
        message_parts.append(
            "\n\n_I'll automatically continue once you complete authorization._"
        )

        text = "".join(message_parts)

        try:
            # Send message to Slack
            await self.slack_client.chat_postMessage(
                channel=self.channel_id,
                thread_ts=self.thread_ts,
                text=text,
                mrkdwn=True,
            )

            logger.info(
                f"[SlackAuthTool] Sent auth link for {service} to "
                f"channel {self.channel_id}, thread {self.thread_ts}"
            )

            return f"✅ Authorization link sent to user for {service}"

        except Exception as e:
            logger.error(
                f"[SlackAuthTool] Failed to send message: {e}", exc_info=True
            )
            # Fallback: return message for agent to relay
            return f"❌ Could not send auth link. Please authorize {service} at: {auth_url}"


class ConsoleAuthToolHandler(AuthToolHandler):
    """
    Console/CLI handler that prints auth links to stdout.

    Useful for development, testing, or CLI applications.

    Example:
        >>> handler = ConsoleAuthToolHandler()
    """

    async def handle_auth_request(
        self,
        service: str,
        reason: str,
        challenge: dict[str, Any],
    ) -> str:
        """Print auth link to console."""
        auth_url = challenge.get("authorization_url", "")

        print("\n" + "=" * 60)
        print("🔐 AUTHORIZATION REQUIRED")
        print("=" * 60)
        print(f"Service: {service}")
        if reason:
            print(f"Reason:  {reason}")
        print(f"\nAuthorize here: {auth_url}")
        print("=" * 60 + "\n")

        return f"Authorization link displayed in console for {service}"

