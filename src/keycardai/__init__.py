"""
Keycardai Python SDK - A namespace package for Keycard services.

This is a workspace root namespace package. All functionality is provided
by workspace member packages under the keycardai namespace.
"""

__version__ = "0.0.1"

# This makes keycardai a namespace package
__path__ = __import__("pkgutil").extend_path(__path__, __name__)
