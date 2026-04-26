"""Keycard-aware authorization decorators for Starlette/FastAPI.

This module provides two decorators that build on the standard Starlette
authentication framework (``request.user`` / ``request.auth``):

- :func:`requires` is a drop-in replacement for
  ``starlette.authentication.requires`` that returns RFC 6750
  401 ``WWW-Authenticate`` challenges for anonymous requests instead
  of stock ``HTTPException(403)``.
- :func:`grant` performs delegated OAuth 2.0 token exchange (RFC 8693)
  and injects an :class:`AccessContext` parameter into the decorated
  endpoint so it can call downstream APIs on behalf of the user.

Both decorators expect the request to have already passed through
``starlette.middleware.authentication.AuthenticationMiddleware`` wired
to a ``KeycardAuthBackend``.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from keycardai.oauth.server.access_context import AccessContext
from keycardai.oauth.server.exceptions import MissingAccessContextError
from keycardai.oauth.server.token_exchange import exchange_tokens_for_resources
from starlette._utils import is_async_callable
from starlette.authentication import has_required_scope
from starlette.exceptions import HTTPException
from starlette.requests import Request

from .middleware.bearer import (
    KeycardUser,
    _build_unauthorized_response,
)

if TYPE_CHECKING:
    from .provider import AuthProvider


def _find_request(args: tuple, kwargs: dict) -> Request | None:
    for value in args:
        if isinstance(value, Request):
            return value
    for value in kwargs.values():
        if isinstance(value, Request):
            return value
    return None


def requires(
    scopes: str | Sequence[str],
    status_code: int = 403,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Keycard-aware drop-in for ``starlette.authentication.requires``.

    Behavior:

    - If the request is anonymous (``not request.user.is_authenticated``),
      returns a 401 response with an RFC 6750 ``WWW-Authenticate`` header
      that includes the ``resource_metadata=`` URL (RFC 9728) computed from
      the current request, instead of stock ``HTTPException(403)``.
    - If the user is authenticated but missing one of the required scopes,
      raises ``HTTPException(status_code)`` (default 403), matching the
      stock decorator's "authenticated but unauthorized" behavior.

    The ``redirect`` argument from stock ``requires`` is intentionally
    omitted - browser redirects do not apply to OAuth 2.0 protected
    resources.
    """
    scopes_list = [scopes] if isinstance(scopes, str) else list(scopes)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(func)
        request_index: int | None = None
        for idx, parameter in enumerate(sig.parameters.values()):
            if parameter.name == "request":
                request_index = idx
                break
        if request_index is None:
            raise TypeError(
                f"@keycardai.starlette.requires expects a 'request' "
                f"parameter on {func.__qualname__}"
            )

        if is_async_callable(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                request = kwargs.get(
                    "request",
                    args[request_index] if request_index < len(args) else None,
                )
                if not isinstance(request, Request):
                    raise TypeError(
                        "@keycardai.starlette.requires expects 'request' to be "
                        "a starlette.requests.Request instance"
                    )
                if not request.user.is_authenticated:
                    return _build_unauthorized_response(request)
                if not has_required_scope(request, scopes_list):
                    raise HTTPException(status_code=status_code)
                return await func(*args, **kwargs)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            request = kwargs.get(
                "request",
                args[request_index] if request_index < len(args) else None,
            )
            if not isinstance(request, Request):
                raise TypeError(
                    "@keycardai.starlette.requires expects 'request' to be "
                    "a starlette.requests.Request instance"
                )
            if not request.user.is_authenticated:
                return _build_unauthorized_response(request)
            if not has_required_scope(request, scopes_list):
                raise HTTPException(status_code=status_code)
            return func(*args, **kwargs)

        return sync_wrapper

    return decorator


def _get_access_context_param(func: Callable) -> tuple[str, int] | None:
    sig = inspect.signature(func)
    for index, value in enumerate(sig.parameters.values()):
        if value.annotation is AccessContext:
            return value.name, index
    return None


def _safe_signature_for_fastapi(func: Callable) -> inspect.Signature:
    """Drop the ``AccessContext`` parameter from the public signature.

    FastAPI builds endpoint schemas from ``inspect.signature(func)``; a parameter
    typed as ``AccessContext`` would otherwise be treated as a request field.
    """
    sig = inspect.signature(func)
    safe_params = [p for p in sig.parameters.values() if p.annotation is not AccessContext]
    return sig.replace(parameters=safe_params)


def grant(
    provider: AuthProvider,
    resources: str | list[str],
    user_identifier: Callable[..., str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator factory for delegated OAuth 2.0 token exchange (RFC 8693).

    Wraps an endpoint so that, after authentication, the SDK exchanges the
    user's verified bearer token for one access token per ``resources`` entry
    and stores them on an ``AccessContext`` parameter passed into the endpoint.

    Errors from the exchange are stored per-resource on the ``AccessContext``
    rather than raised; the decorated function should call
    ``access.has_errors()`` / ``access.get_errors()`` and decide how to
    respond.

    Args:
        provider: The ``AuthProvider`` instance whose OAuth client and
            application credential perform the exchange.
        resources: Resource URL or list of resource URLs to exchange for.
        user_identifier: Optional callable that, given the endpoint's
            keyword arguments, returns the user identifier to impersonate
            (RFC 8693 substitute-user exchange). When set, the exchange
            uses ``client.impersonate(...)``.

    The decorated function must declare an ``AccessContext``-typed parameter;
    otherwise ``MissingAccessContextError`` is raised at decoration time.
    The ``AccessContext`` parameter is hidden from FastAPI introspection via
    ``__signature__`` rewriting.
    """
    resource_list = [resources] if isinstance(resources, str) else list(resources)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        access_ctx_param = _get_access_context_param(func)
        if access_ctx_param is None:
            raise MissingAccessContextError(
                function_name=getattr(func, "__name__", None),
                parameters=[
                    p.name for p in inspect.signature(func).parameters.values()
                ],
            )

        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            request = _find_request(args, kwargs)
            if request is None:
                raise RuntimeError(
                    "@auth.grant requires the decorated function to accept "
                    "a starlette.requests.Request parameter."
                )

            if not request.user.is_authenticated:
                return _build_unauthorized_response(request)

            user: KeycardUser = request.user
            access_ctx = kwargs.get(access_ctx_param[0])
            if access_ctx is None:
                access_ctx = AccessContext()
                kwargs[access_ctx_param[0]] = access_ctx

            auth_info: dict[str, str | None] = {
                "access_token": user.access_token,
                "zone_id": user.zone_id,
                "resource_client_id": user.resource_server_url,
                "resource_server_url": user.resource_server_url,
            }

            if provider.enable_multi_zone and not user.zone_id:
                access_ctx.set_error(
                    {
                        "message": "Zone ID required for multi-zone "
                        "configuration but not found."
                    }
                )
                return await _invoke(func, is_async, args, kwargs)

            try:
                client = await provider._get_or_create_client(auth_info)
            except Exception as e:
                access_ctx.set_error(
                    {
                        "message": "Failed to initialize OAuth client.",
                        "raw_error": str(e),
                    }
                )
                return await _invoke(func, is_async, args, kwargs)

            if client is None:
                access_ctx.set_error(
                    {
                        "message": "OAuth client not available. "
                        "Server configuration issue."
                    }
                )
                return await _invoke(func, is_async, args, kwargs)

            resolved_user_id: str | None = None
            if user_identifier is not None:
                try:
                    resolved_user_id = user_identifier(**kwargs)
                except Exception as e:
                    access_ctx.set_error(
                        {
                            "message": "Failed to resolve user_identifier.",
                            "raw_error": str(e),
                        }
                    )
                    return await _invoke(func, is_async, args, kwargs)

            await exchange_tokens_for_resources(
                client=client,
                resources=resource_list,
                subject_token=user.access_token,
                access_context=access_ctx,
                application_credential=provider.application_credential,
                auth_info=auth_info,
                user_identifier=resolved_user_id,
            )

            return await _invoke(func, is_async, args, kwargs)

        wrapper.__signature__ = _safe_signature_for_fastapi(func)  # type: ignore[attr-defined]
        return wrapper

    return decorator


async def _invoke(func: Callable, is_async: bool, args: tuple, kwargs: dict) -> Any:
    if is_async:
        return await func(*args, **kwargs)
    return func(*args, **kwargs)
