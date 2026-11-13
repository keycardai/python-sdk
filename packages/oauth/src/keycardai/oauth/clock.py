"""Clock injected into AsyncClient for the current time and for waits.

Tests pass a fake clock to run time-dependent code without real delays.
"""

import asyncio
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """Gives the current time and waits for a duration."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware datetime."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Wait ``seconds``. Task cancellation ends the wait."""
        ...


class SystemClock:
    """The real clock: UTC wall time and ``asyncio.sleep``."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
