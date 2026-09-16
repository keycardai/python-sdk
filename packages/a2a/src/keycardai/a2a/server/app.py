"""Keycard wiring primitives for a2a-sdk agent services.

This module is glue, not a parallel server abstraction. Customers compose
these primitives into their own ``a2a-sdk`` setup:

- For the A2A JSONRPC mount, use
  ``KeycardAuthBackend(verifier, require_authentication=True)`` from
  keycardai-starlette inside Starlette's ``AuthenticationMiddleware``.
  The kwarg flips the default mixed-route behavior to "every path on this
  mount needs auth," which matches the JSONRPC dispatcher's lack of a
  per-route gate.
- ``KeycardServerCallContextBuilder``: a ``ServerCallContextBuilder``
  subclass that propagates the verified ``KeycardUser`` into
  ``ServerCallContext.state``. Pass to
  ``a2a.server.routes.create_jsonrpc_routes``.
- ``keycard_user``: typed accessor executors call on their
  ``RequestContext`` to get that ``KeycardUser`` back (or ``None`` for an
  unauthenticated request), so the token for delegated exchange is
  ``keycard_user(context).access_token`` rather than a state-key lookup.
- ``build_agent_card_from_config``: construct an ``a2a.types.AgentCard``
  protobuf from an ``AgentServiceConfig``. Pass to
  ``a2a.server.routes.create_agent_card_routes``.

For a runnable composed-server example see
``packages/a2a/examples/keycard_protected_server/``.
"""

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.requests import Request

from keycardai.starlette import KeycardUser

from ..config import AgentServiceConfig

KEYCARD_USER_STATE_KEY = "keycard_user"
"""``ServerCallContext.state`` key holding the verified ``KeycardUser``."""

ACCESS_TOKEN_STATE_KEY = "access_token"
"""Legacy ``ServerCallContext.state`` key holding the bare bearer token.

Still written for executors built against keycardai-a2a 0.4.x; new code
reads ``keycard_user(context).access_token`` instead.
"""


class KeycardServerCallContextBuilder(DefaultServerCallContextBuilder):
    """Propagate the verified ``KeycardUser`` into a2a-sdk's ``ServerCallContext``.

    The default builder wraps ``request.user`` in a thin ``StarletteUser``
    adapter that exposes only ``is_authenticated`` and ``user_name``,
    dropping the ``access_token`` / ``client_id`` / ``scopes`` / ``zone_id``
    fields. Agent executors need at least the ``access_token`` for
    delegated token exchange, so this subclass stashes the full
    ``KeycardUser`` in ``ServerCallContext.state`` for executors to read
    back through :func:`keycard_user`.

    For unauthenticated requests (``request.user`` is not a
    ``KeycardUser``) the builder leaves the state entries unset rather
    than falling back to a placeholder, so :func:`keycard_user` returns
    ``None``.
    """

    def build(self, request: Request) -> ServerCallContext:
        ctx = super().build(request)
        user = request.scope.get("user")
        if isinstance(user, KeycardUser):
            ctx.state[KEYCARD_USER_STATE_KEY] = user
            ctx.state[ACCESS_TOKEN_STATE_KEY] = user.access_token
        return ctx


def keycard_user(context: RequestContext | ServerCallContext | None) -> KeycardUser | None:
    """Return the verified caller stored by ``KeycardServerCallContextBuilder``.

    Accepts the ``RequestContext`` an ``AgentExecutor.execute`` receives, or
    the underlying ``ServerCallContext`` directly. Returns ``None`` when
    there is no call context or the request was unauthenticated, so an
    executor can gate on it::

        caller = keycard_user(context)
        if caller is None:
            raise PermissionError("unauthenticated")
        subject_token = caller.access_token
    """
    call_context = context.call_context if isinstance(context, RequestContext) else context
    if call_context is None:
        return None
    user = call_context.state.get(KEYCARD_USER_STATE_KEY)
    return user if isinstance(user, KeycardUser) else None


def build_agent_card_from_config(config: AgentServiceConfig) -> AgentCard:
    """Construct an a2a-sdk 1.x AgentCard from an ``AgentServiceConfig``.

    Returns a protobuf ``AgentCard`` suitable for passing to
    ``a2a.server.routes.create_agent_card_routes`` and
    ``a2a.server.request_handlers.DefaultRequestHandler``.
    """
    skills = [
        AgentSkill(
            id=cap,
            name=cap.replace("_", " ").title(),
            description=f"{cap} capability",
            tags=[cap],
        )
        for cap in config.capabilities
    ]
    return AgentCard(
        name=config.service_name,
        description=config.description or f"{config.service_name} agent service",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=f"{config.identity_url}/a2a/jsonrpc",
                protocol_binding="jsonrpc",
                protocol_version="1.0",
            ),
        ],
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            extended_agent_card=False,
        ),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=skills,
    )
