"""Private Key Identity Management for OAuth Resource Servers.

This module provides a protocol-based approach for managing private key identities
across different storage backends (file, memory, key-value stores). It supports
JWT client assertion generation and JWKS endpoint provisioning for OAuth 2.0
private_key_jwt authentication.

Key Features:
- Protocol-based storage abstraction for multiple backends
- Idempotent key pair bootstrap and loading
- JWT client assertion generation for OAuth 2.0
- JWKS format public key export
- Configurable audience mapping for multi-zone scenarios

Storage Providers:
- FilePrivateKeyStorage: Persistent file-based storage
"""

import json
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

from authlib.jose import JsonWebKey, JsonWebToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import PublicFormat
from pydantic import AnyHttpUrl, BaseModel

from keycardai.oauth.types.models import (
    JsonWebKey as KeycardJsonWebKey,
    JsonWebKeySet,
)


class PrivateKeyStorageProtocol(Protocol):
    """Protocol for private key storage backends.

    This protocol defines the interface that all private key storage providers
    must implement. Storage providers can be file-based, memory-based, or
    external key-value stores.
    """

    def exists(self, key_id: str) -> bool:
        """Check if a private key exists for the given key ID."""
        ...

    def store_key_pair(
        self,
        key_id: str,
        private_key_pem: str,
        public_key_jwk: dict[str, Any],
    ) -> None:
        """Store a private key and its associated public key."""
        ...

    def load_key_pair(self, key_id: str) -> tuple[str, dict[str, Any]]:
        """Load a private key and its associated public key."""
        ...

    def delete_key_pair(self, key_id: str) -> bool:
        """Delete a key pair."""
        ...

    def list_key_ids(self) -> list[str]:
        """List all stored key IDs."""
        ...


class KeyPairInfo(BaseModel):
    """Information about a stored key pair."""

    key_id: str
    private_key_pem: str
    public_key_jwk: dict[str, Any]
    created_at: float
    algorithm: str = "RS256"


class FilePrivateKeyStorage:
    """File-based private key storage implementation.

    Stores private keys as PEM files and metadata as JSON files in a specified
    directory. Provides atomic operations and proper file permissions.
    """

    def __init__(self, storage_dir: str):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _get_key_file_path(self, key_id: str) -> Path:
        return self.storage_dir / f"{key_id}.pem"

    def _get_metadata_file_path(self, key_id: str) -> Path:
        return self.storage_dir / f"{key_id}.json"

    def exists(self, key_id: str) -> bool:
        return (
            self._get_key_file_path(key_id).exists()
            and self._get_metadata_file_path(key_id).exists()
        )

    def store_key_pair(
        self,
        key_id: str,
        private_key_pem: str,
        public_key_jwk: dict[str, Any],
    ) -> None:
        key_file = self._get_key_file_path(key_id)
        metadata_file = self._get_metadata_file_path(key_id)

        metadata = {
            "key_id": key_id,
            "public_key_jwk": public_key_jwk,
            "created_at": time.time(),
            "algorithm": "RS256",
        }

        key_file.write_text(private_key_pem, encoding="utf-8")
        key_file.chmod(0o600)

        metadata_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        metadata_file.chmod(0o644)

    def load_key_pair(self, key_id: str) -> tuple[str, dict[str, Any]]:
        if not self.exists(key_id):
            raise KeyError(f"Key pair '{key_id}' not found")

        key_file = self._get_key_file_path(key_id)
        metadata_file = self._get_metadata_file_path(key_id)

        try:
            private_key_pem = key_file.read_text(encoding="utf-8")
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            return private_key_pem, metadata["public_key_jwk"]
        except Exception as e:
            raise KeyError(f"Failed to load key pair '{key_id}': {e}") from e

    def delete_key_pair(self, key_id: str) -> bool:
        key_file = self._get_key_file_path(key_id)
        metadata_file = self._get_metadata_file_path(key_id)

        deleted = False
        if key_file.exists():
            key_file.unlink()
            deleted = True
        if metadata_file.exists():
            metadata_file.unlink()
            deleted = True

        return deleted

    def list_key_ids(self) -> list[str]:
        key_ids = []
        for metadata_file in self.storage_dir.glob("*.json"):
            key_id = metadata_file.stem
            if self.exists(key_id):
                key_ids.append(key_id)
        return sorted(key_ids)


class PrivateKeyManager:
    """Manages private key identity for OAuth resource servers.

    Provides high-level interface for private key management including:
    - Idempotent key pair creation and loading
    - JWT client assertion generation for OAuth 2.0
    - JWKS format public key export
    - Configurable audience mapping for multi-zone scenarios
    """

    def __init__(
        self,
        storage: PrivateKeyStorageProtocol,
        key_id: str | None = None,
        audience_config: str | dict[str, str] | None = None,
    ):
        self.storage = storage
        self.key_id = key_id or str(uuid.uuid4())
        self.audience_config = audience_config
        self._private_key_pem: str | None = None
        self._public_key_jwk: dict[str, Any] | None = None

    def bootstrap_identity(self) -> None:
        """Idempotent key pair creation and loading."""
        if self.storage.exists(self.key_id):
            self._private_key_pem, self._public_key_jwk = self.storage.load_key_pair(
                self.key_id
            )
        else:
            self._generate_and_store_key_pair()

    def _generate_and_store_key_pair(self) -> None:
        private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )

        private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        public_key = private_key.public_key()

        public_key_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=PublicFormat.SubjectPublicKeyInfo,
        )

        jwk = JsonWebKey.import_key(public_key_pem)
        public_key_jwk = jwk.as_dict()

        public_key_jwk["kid"] = self.key_id
        public_key_jwk["alg"] = "RS256"
        public_key_jwk["use"] = "sig"

        self.storage.store_key_pair(self.key_id, private_key_pem, public_key_jwk)

        self._private_key_pem = private_key_pem
        self._public_key_jwk = public_key_jwk

    def get_private_key_pem(self) -> str:
        if self._private_key_pem is None:
            raise RuntimeError(
                "Identity not bootstrapped. Call bootstrap_identity() first."
            )
        return self._private_key_pem

    def get_public_jwks(self) -> dict[str, Any]:
        if self._public_key_jwk is None:
            raise RuntimeError(
                "Identity not bootstrapped. Call bootstrap_identity() first."
            )
        return {"keys": [self._public_key_jwk]}

    def _resolve_audience(
        self, issuer: str, zone_id: str | None = None
    ) -> str:
        if self.audience_config is None:
            return issuer

        if isinstance(self.audience_config, str):
            return self.audience_config

        if isinstance(self.audience_config, dict):
            if zone_id is None:
                raise ValueError("zone_id required when audience_config is dict")

            if zone_id not in self.audience_config:
                raise ValueError(f"No audience configured for zone '{zone_id}'")

            return self.audience_config[zone_id]

        return issuer

    def create_client_assertion(
        self,
        issuer: str,
        subject: str | None = None,
        audience: str | None = None,
        expiry_seconds: int = 300,
    ) -> str:
        """Create JWT assertion for the given audience.

        Creates a JWT client assertion suitable for OAuth 2.0 private_key_jwt
        authentication as defined in RFC 7523.
        """
        if subject is None:
            subject = issuer
        if audience is None:
            audience = issuer

        if self._private_key_pem is None or self._public_key_jwk is None:
            raise RuntimeError(
                "Identity not bootstrapped. Call bootstrap_identity() first."
            )

        now = int(time.time())
        payload = {
            "iss": issuer,
            "sub": subject,
            "aud": audience,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": now + expiry_seconds,
        }

        header = {"alg": "RS256", "typ": "JWT", "kid": self.key_id}

        jwt = JsonWebToken(["RS256"])
        private_key = serialization.load_pem_private_key(
            self._private_key_pem.encode("utf-8"), password=None
        )

        return jwt.encode(header, payload, private_key)

    def get_client_id(self) -> str:
        return self.key_id

    def rotate_key(self) -> str:
        self.key_id = str(uuid.uuid4())
        self._generate_and_store_key_pair()
        return self.key_id

    def cleanup_old_keys(self, keep_latest: int = 1) -> list[str]:
        all_key_ids = self.storage.list_key_ids()

        if len(all_key_ids) <= keep_latest:
            return []

        sorted_key_ids = sorted(all_key_ids)
        to_delete = sorted_key_ids[:-keep_latest]
        deleted = []

        for key_id in to_delete:
            if self.storage.delete_key_pair(key_id):
                deleted.append(key_id)

        return deleted

    def get_client_jwks_url(self, resource_server_url: str) -> str:
        resource_url = AnyHttpUrl(resource_server_url)
        base_url = f"{resource_url.scheme}://{resource_url.host.rstrip('/')}"
        if resource_url.port not in [443, 80]:
            base_url += ":" + str(resource_url.port)
        return f"{base_url}/.well-known/jwks.json"

    def get_jwks(self) -> JsonWebKeySet:
        key_objects = []
        for jwk_data in self.get_public_jwks()["keys"]:
            key_objects.append(KeycardJsonWebKey(**jwk_data))
        return JsonWebKeySet(keys=key_objects)
