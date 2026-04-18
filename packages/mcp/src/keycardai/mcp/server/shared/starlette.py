"""Starlette request helpers.

Re-exported from keycardai.starlette_oauth.shared.starlette for backward compatibility.
Canonical import: ``from keycardai.starlette_oauth.shared import get_base_url``
"""

from keycardai.starlette_oauth.shared.starlette import SUPPORTED_PROTOCOLS, get_base_url

__all__ = ["SUPPORTED_PROTOCOLS", "get_base_url"]
