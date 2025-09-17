"""JWKS endpoint handler for OAuth authentication.

This module provides the JWKS (JSON Web Key Set) endpoint implementation
that serves the public keys used for JWT token verification.
"""

from collections.abc import Callable

from starlette.requests import Request
from starlette.responses import JSONResponse

from keycardai.oauth.types import JsonWebKey, JsonWebKeySet


def jwks_endpoint() -> Callable:
    """Create a JWKS endpoint that returns a dummy RSA key for testing.

    Returns:
        Callable endpoint that serves JWKS data
    """
    def wrapper(request: Request) -> JSONResponse:
        dummy_key = JsonWebKey(
            kty="RSA",
            n="sqnOhfwgp9Z7cUxUQMo3jNuox84AZJ1_BdE22XE2UQKcD_4cYBZCIdklrC1ToSmrgdOjyXb1vUtqKQ-FY6vQSjXmLHH_u0xvtEO-cc2ppoZSWyA8_oxJwFgaCMG6kNOeQd8ac7YAsfJ-6WgPm5-fVKSD1RQmd0L1qBOmlnOey46x8IX_fS7m7wVLe85B2_s8ssQ41X5sXO_a_mofAztjeudSzyxZavuUD1s0oRClDTD7NvoRYicBaNUgRJj_spMLwzncKbgbK-pBXabLdmJFnhFBGQO6R5UfT3O_cGDgOvOYt-RqRkPDiFURxZGjl7E4ubpMjPxm2v1ZzXAry3nFKQ",
            e="AQAB",
            alg="RS256",
            use="sig",
            kid="0198ed48-5d57-739b-ad11-265ed3f2b9cf"
        )

        jwks = JsonWebKeySet(keys=[dummy_key])

        return JSONResponse(
            content=jwks.model_dump(exclude_none=True),
            status_code=200,
            headers={"Content-Type": "application/json"}
        )

    return wrapper
