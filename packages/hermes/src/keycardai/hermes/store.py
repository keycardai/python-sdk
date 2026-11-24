"""The sign-in store: one 0600 file per (zone, profile, actor) under the Hermes home."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

EXPIRY_SKEW_SECONDS = 60.0
LOCK_POLL_SECONDS = 0.02


@dataclass(frozen=True)
class SignIn:
    """A stored sign-in. ``expires_at`` is epoch seconds of the access token."""

    client_id: str
    refresh_token: str | None
    access_token: str
    expires_at: float

    def near_expiry(self, skew: float = EXPIRY_SKEW_SECONDS, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at - skew


def _actor_key(actor: str) -> str:
    """A filename-safe key that is unique per actor: a readable prefix plus a
    hash of the full id, so `alice@example.com` and `alice_example.com` never
    share a file."""
    prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", actor)[:32]
    digest = hashlib.sha256(actor.encode()).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _zone_key(zone_url: str) -> str:
    return hashlib.sha256(zone_url.rstrip("/").encode()).hexdigest()[:16]


def _private_dir(path: Path) -> None:
    """Create ``path`` and its parent 0700; ``mkdir(mode=)`` only applies to
    the leaf, so the parent is created on its own first."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.mkdir(exist_ok=True, mode=0o700)


def _write_private(path: Path, payload: dict[str, object]) -> None:
    """Write a 0600 temp file beside ``path`` (``mkstemp`` creates it 0600
    under a unique name, so concurrent writers never share one) and rename it
    over the old one."""
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


class SignInStore:
    """Sign-ins and the registered public client for one profile and zone.

    The profile is the Hermes home, so the key (zone, profile, actor) becomes
    ``<home>/keycard/<zone digest>/signin-<actor digest>.json``; the public
    client ``login`` registered lives beside them in ``client.json`` and is
    shared by every actor in the zone. Directories are 0700, files are created
    0600 and replaced by rename.
    """

    def __init__(self, home: Path | str, zone_url: str) -> None:
        self.home = Path(home)
        self.zone_url = zone_url
        self.dir = self.home / "keycard" / _zone_key(zone_url)

    def path(self, actor: str) -> Path:
        return self.dir / f"signin-{_actor_key(actor)}.json"

    def load(self, actor: str) -> SignIn | None:
        try:
            data = json.loads(self.path(actor).read_text())
        except FileNotFoundError:
            return None
        if data.get("actor") != actor:
            return None
        return SignIn(
            client_id=str(data["client_id"]),
            refresh_token=data.get("refresh_token"),
            access_token=str(data["access_token"]),
            expires_at=float(data["expires_at"]),
        )

    def save(self, actor: str, sign_in: SignIn) -> None:
        _private_dir(self.dir)
        _write_private(self.path(actor), {"actor": actor, **asdict(sign_in)})

    def client_id(self) -> str | None:
        try:
            return str(json.loads((self.dir / "client.json").read_text())["client_id"])
        except FileNotFoundError:
            return None

    def save_client_id(self, client_id: str) -> None:
        _private_dir(self.dir)
        _write_private(self.dir / "client.json", {"client_id": client_id})

    @contextmanager
    def lock(self, actor: str) -> Iterator[None]:
        """An exclusive lock on the sign-in, across threads and processes.

        ``flock`` on a lock file beside the sign-in: a second process blocks
        on the kernel lock, and a second thread of this process blocks on it
        too because each ``with`` opens its own descriptor.
        """
        _private_dir(self.dir)
        lock_path = self.path(actor).with_suffix(".lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @asynccontextmanager
    async def alock(self, actor: str, *, poll: float = LOCK_POLL_SECONDS) -> AsyncIterator[None]:
        """``lock`` for a coroutine: the same ``flock`` on the same lock file,
        so it excludes ``lock`` holders in any thread or process, taken
        without blocking the event loop.

        A non-blocking ``flock`` is retried every ``poll`` seconds while
        another descriptor holds the lock. No call waits in the kernel, so a
        cancelled waiter only closes its descriptor and leaves no worker
        thread behind that could take the lock later.
        """
        _private_dir(self.dir)
        lock_path = self.path(actor).with_suffix(".lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(poll)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
