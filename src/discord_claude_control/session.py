"""
Idle/active session state machine that owns the power request.

Behavior:

- A `ping(reason)` call records activity. If the session is currently idle,
  it transitions to active and acquires the power request so the PC stays
  in S0 instead of dropping into Modern Standby.
- After `idle_timeout_seconds` elapse with no further pings the session
  transitions back to idle and releases the power request.
- Every transition logs one INFO line with the named reason, e.g.
  "session active (acquired: user message)" /
  "session idle (released: idle 600s)".

The synchronous core (`ping`, `check_for_idle_transition`, `is_active`,
`seconds_until_idle`) is fully testable without async or real time. The
`run_idle_loop` coroutine is the trivial async wrapper that polls the
core and waits on an asyncio.Event for early wake-ups.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from .power import PowerRequest

log = logging.getLogger(__name__)


class SessionState:
    def __init__(
        self,
        idle_timeout_seconds: float,
        power_request: PowerRequest,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be > 0")
        self._idle_timeout = idle_timeout_seconds
        self._power = power_request
        self._clock = clock
        self._last_activity: float | None = None
        self._is_active = False
        self._wake = asyncio.Event()

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def idle_timeout_seconds(self) -> float:
        return self._idle_timeout

    @property
    def seconds_until_idle(self) -> float | None:
        """How many seconds until the idle timeout expires, or None if idle."""
        if not self._is_active or self._last_activity is None:
            return None
        elapsed = self._clock() - self._last_activity
        return max(0.0, self._idle_timeout - elapsed)

    def ping(self, reason: str) -> None:
        self._last_activity = self._clock()
        if not self._is_active:
            self._is_active = True
            self._power.acquire()
            log.info("session active (acquired: %s)", reason)
        self._wake.set()

    def check_for_idle_transition(self) -> bool:
        """If active and the idle deadline has passed, release power and go idle.

        Returns True if a transition happened, False otherwise.
        """
        if not self._is_active or self._last_activity is None:
            return False
        elapsed = self._clock() - self._last_activity
        if elapsed < self._idle_timeout:
            return False
        self._is_active = False
        self._power.release()
        log.info("session idle (released: idle %.0fs)", self._idle_timeout)
        return True

    def force_release(self, reason: str) -> None:
        """Force the session to idle (used at shutdown)."""
        if not self._is_active:
            return
        self._is_active = False
        self._power.release()
        log.info("session idle (released: %s)", reason)

    async def run_idle_loop(self, stop_event: asyncio.Event) -> None:
        """Watch for idle transitions until `stop_event` is set."""
        while not stop_event.is_set():
            if not self._is_active:
                # Sleep until a ping wakes us or we're told to stop.
                self._wake.clear()
                await _wait_for_first(self._wake, stop_event)
                continue

            remaining = self.seconds_until_idle
            if remaining is None or remaining <= 0:
                self.check_for_idle_transition()
                continue

            self._wake.clear()
            try:
                await asyncio.wait_for(
                    _wait_for_first(self._wake, stop_event),
                    timeout=remaining,
                )
            except TimeoutError:
                self.check_for_idle_transition()


async def _wait_for_first(*events: asyncio.Event) -> None:
    """Return when any of the events is set."""
    import contextlib

    waiters = [asyncio.create_task(e.wait()) for e in events]
    try:
        _done, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    finally:
        for task in waiters:
            if not task.done():
                task.cancel()
