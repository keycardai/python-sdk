"""Private Key Identity Management for MCP Servers.

Re-exported from keycardai.oauth.server.private_key for backward compatibility.
Canonical import: ``from keycardai.oauth.server.private_key import PrivateKeyManager``
"""

from keycardai.oauth.server.private_key import (
    FilePrivateKeyStorage,
    KeyPairInfo,
    PrivateKeyManager,
    PrivateKeyStorageProtocol,
)

__all__ = [
    "FilePrivateKeyStorage",
    "KeyPairInfo",
    "PrivateKeyManager",
    "PrivateKeyStorageProtocol",
]
