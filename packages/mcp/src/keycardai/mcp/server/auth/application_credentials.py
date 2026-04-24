"""Application Credential Providers for Token Exchange.

Re-exported from keycardai.oauth.server.credentials for backward compatibility.
Canonical import: ``from keycardai.oauth.server.credentials import ClientSecret``
"""

from keycardai.oauth.server.credentials import (
    ApplicationCredential,
    ClientSecret,
    EKSWorkloadIdentity,
    WebIdentity,
)

__all__ = [
    "ApplicationCredential",
    "ClientSecret",
    "EKSWorkloadIdentity",
    "WebIdentity",
]
