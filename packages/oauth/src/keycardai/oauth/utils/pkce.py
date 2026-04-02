"""PKCE (Proof Key for Code Exchange) utilities for OAuth 2.0 (RFC 7636).

This module implements PKCE utilities for OAuth 2.0 public clients
as defined in RFC 7636.

RFC 7636: Proof Key for Code Exchange by OAuth Public Clients
https://datatracker.ietf.org/doc/html/rfc7636

Key Features:
- PKCE challenge/verifier generation and validation
- Support for both S256 and plain methods (S256 recommended)
- Cryptographically secure parameter generation

Security Benefits:
- Prevents authorization code interception attacks
- Eliminates client secret management for public clients
- Enables secure OAuth flows for mobile and SPA applications
"""

import base64
import hashlib
import secrets

from pydantic import BaseModel


class PKCEChallenge(BaseModel):
    """PKCE Challenge and Verifier pair for RFC 7636.

    Reference: https://datatracker.ietf.org/doc/html/rfc7636#section-4.1
    """

    code_verifier: str
    code_challenge: str
    code_challenge_method: str = "S256"


class PKCEMethods:
    """PKCE code challenge methods defined in RFC 7636 Section 4.2.

    Reference: https://datatracker.ietf.org/doc/html/rfc7636#section-4.2
    """

    PLAIN = "plain"  # NOT RECOMMENDED - use only if S256 unavailable
    S256 = "S256"  # RECOMMENDED - SHA256 hash of verifier


class PKCEGenerator:
    """PKCE challenge and verifier generator for RFC 7636.

    Generates cryptographically secure PKCE parameters for OAuth 2.0
    authorization code flows with public clients.

    Reference: https://datatracker.ietf.org/doc/html/rfc7636

    Example:
        generator = PKCEGenerator()

        # Generate PKCE parameters (recommended S256 method)
        challenge = generator.generate_pkce_pair()
        print(f"Verifier: {challenge.code_verifier}")
        print(f"Challenge: {challenge.code_challenge}")

        # Use plain method (NOT RECOMMENDED)
        plain_challenge = generator.generate_pkce_pair(method="plain")

        # Validate PKCE parameters
        is_valid = generator.validate_pkce_pair(
            code_verifier="abc123...",
            code_challenge="xyz789...",
            method="S256"
        )
    """

    @staticmethod
    def generate_code_verifier(length: int = 128) -> str:
        """Generate a cryptographically random code verifier.

        Creates a code verifier as defined in RFC 7636 Section 4.1 using
        unreserved characters [A-Z] [a-z] [0-9] "-" "." "_" "~".

        Args:
            length: Length of verifier (43-128 characters, default 128)

        Returns:
            Base64url-encoded random string suitable as code verifier

        Raises:
            ValueError: If length is not between 43-128 characters

        Reference: https://datatracker.ietf.org/doc/html/rfc7636#section-4.1
        """
        if length < 43 or length > 128:
            raise ValueError("Code verifier length must be between 43 and 128 characters")
        # Scale byte count to requested length so we only generate as much
        # entropy as needed (base64url expands 3 bytes into 4 chars).
        nbytes = (length * 3 + 3) // 4
        return secrets.token_urlsafe(nbytes)[:length]

    @staticmethod
    def generate_code_challenge(verifier: str, method: str = "S256") -> str:
        """Generate code challenge from verifier using specified method.

        Creates a code challenge as defined in RFC 7636 Section 4.2.

        Args:
            verifier: Code verifier string
            method: Challenge method ("S256" or "plain")

        Returns:
            Code challenge string

        Raises:
            ValueError: If method is not supported

        Reference: https://datatracker.ietf.org/doc/html/rfc7636#section-4.2
        """
        if method == PKCEMethods.S256:
            digest = hashlib.sha256(verifier.encode("ascii")).digest()
            return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        elif method == PKCEMethods.PLAIN:
            return verifier
        else:
            raise ValueError(f"Unsupported PKCE method: {method}")

    def generate_pkce_pair(
        self, method: str = "S256", verifier_length: int = 128
    ) -> PKCEChallenge:
        """Generate a complete PKCE challenge/verifier pair.

        Creates both code verifier and challenge using specified method.
        S256 method is recommended for security.

        Args:
            method: Challenge method ("S256" recommended, "plain" for legacy)
            verifier_length: Length of code verifier (43-128 characters)

        Returns:
            PKCEChallenge containing verifier, challenge, and method

        Reference: https://datatracker.ietf.org/doc/html/rfc7636#section-4
        """
        verifier = self.generate_code_verifier(verifier_length)
        challenge = self.generate_code_challenge(verifier, method)
        return PKCEChallenge(
            code_verifier=verifier,
            code_challenge=challenge,
            code_challenge_method=method,
        )

    @staticmethod
    def validate_pkce_pair(
        code_verifier: str, code_challenge: str, method: str = "S256"
    ) -> bool:
        """Validate that code verifier matches the code challenge.

        Verifies PKCE parameters as defined in RFC 7636 Section 4.6.

        Args:
            code_verifier: Original code verifier
            code_challenge: Code challenge to validate against
            method: Challenge method used ("S256" or "plain")

        Returns:
            True if verifier matches challenge, False otherwise

        Reference: https://datatracker.ietf.org/doc/html/rfc7636#section-4.6
        """
        expected = PKCEGenerator.generate_code_challenge(code_verifier, method)
        return secrets.compare_digest(expected, code_challenge)
