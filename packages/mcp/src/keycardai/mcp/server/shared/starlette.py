"""Starlette request helpers.

Re-exported from keycardai.starlette.shared.starlette for backward compatibility.
Canonical import: ``from keycardai.starlette.shared import get_base_url``
"""

from keycardai.starlette.shared.starlette import SUPPORTED_PROTOCOLS, get_base_url

__all__ = ["SUPPORTED_PROTOCOLS", "get_base_url"]
