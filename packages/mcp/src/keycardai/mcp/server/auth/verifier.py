"""Token verification for Keycard zone-issued tokens.

Re-exported from keycardai.oauth.server.verifier for backward compatibility.
Canonical import: ``from keycardai.oauth.server.verifier import TokenVerifier``
"""

from keycardai.oauth.server.verifier import AccessToken, TokenVerifier

__all__ = ["AccessToken", "TokenVerifier"]
