"""
Tests for SessionState. The synchronous core is driven directly with a
mutable fake clock; the async loop is tested with a small real timeout.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from discord_claude_control.power import PowerRequest
from discord_claude_control.session import SessionState


@dataclass
class RecordingPowerBackend:
    name: str = "recording-power"
    acquired: int = 0
    released: int = 0
    _seq: int = 0
    log: list[tuple[str, object]] = field(default_factory=list)

    def create(self, reason: str) -> object:
        self._seq += 1
        self.log.append(("create", reason))
        return self._seq

    def set_request(self, handle: object) -> None:
        self.acquired += 1
        self.log.append(("set_request", handle))

    def clear_request(self, handle: object) -> None:
        self.released += 1
        self.log.append(("clear_request", handle))

    def close(self, handle: object) -> None:
        self.log.append(("close", handle))


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make() -> tuple[SessionState, RecordingPowerBackend, FakeClock]:
    backend = RecordingPowerBackend()
    pr = PowerRequest(reason="test", backend=backend)
    clock = FakeClock()
    state = SessionState(idle_timeout_seconds=60.0, power_request=pr, clock=clock)
    return state, backend, clock


def test_starts_idle() -> None:
    state, _, _ = _make()
    assert state.is_active is False
    assert state.seconds_until_idle is None


def test_ping_transitions_to_active_and_acquires() -> None:
    state, backend, _ = _make()
    state.ping("user message")
    assert state.is_active is True
    assert backend.acquired == 1
    assert backend.released == 0


def test_second_ping_does_not_reacquire() -> None:
    state, backend, _ = _make()
    state.ping("first")
    state.ping("second")
    assert backend.acquired == 1


def test_check_for_idle_before_timeout_is_noop() -> None:
    state, backend, clock = _make()
    state.ping("hi")
    clock.advance(30)
    assert state.check_for_idle_transition() is False
    assert state.is_active is True
    assert backend.released == 0


def test_check_for_idle_after_timeout_releases() -> None:
    state, backend, clock = _make()
    state.ping("hi")
    clock.advance(60)
    assert state.check_for_idle_transition() is True
    assert state.is_active is False
    assert backend.released == 1


def test_ping_resets_the_clock() -> None:
    state, _backend, clock = _make()
    state.ping("hi")
    clock.advance(50)
    state.ping("more")  # resets last_activity
    clock.advance(50)  # total 100s, but only 50 since last ping
    assert state.check_for_idle_transition() is False
    assert state.is_active is True
    clock.advance(20)
    assert state.check_for_idle_transition() is True


def test_seconds_until_idle_counts_down() -> None:
    state, _, clock = _make()
    state.ping("hi")
    assert state.seconds_until_idle == 60.0
    clock.advance(25)
    assert state.seconds_until_idle == 35.0


def test_seconds_until_idle_is_zero_at_deadline() -> None:
    state, _, clock = _make()
    state.ping("hi")
    clock.advance(60)
    assert state.seconds_until_idle == 0.0


def test_force_release_when_active() -> None:
    state, backend, _ = _make()
    state.ping("hi")
    state.force_release("shutdown")
    assert state.is_active is False
    assert backend.released == 1


def test_force_release_when_idle_is_noop() -> None:
    state, backend, _ = _make()
    state.force_release("shutdown")
    assert backend.released == 0


def test_invalid_idle_timeout_rejected() -> None:
    pr = PowerRequest(backend=RecordingPowerBackend())
    with pytest.raises(ValueError):
        SessionState(idle_timeout_seconds=0, power_request=pr)
    with pytest.raises(ValueError):
        SessionState(idle_timeout_seconds=-5, power_request=pr)


@pytest.mark.asyncio
async def test_idle_loop_releases_after_real_timeout() -> None:
    backend = RecordingPowerBackend()
    pr = PowerRequest(reason="test", backend=backend)
    state = SessionState(idle_timeout_seconds=0.05, power_request=pr)
    stop = asyncio.Event()
    task = asyncio.create_task(state.run_idle_loop(stop))
    state.ping("trigger")
    assert state.is_active is True
    # Give the loop more than enough time to fire the timeout transition.
    await asyncio.sleep(0.2)
    assert state.is_active is False
    assert backend.released == 1
    stop.set()
    await task


@pytest.mark.asyncio
async def test_idle_loop_ping_during_active_resets_timer() -> None:
    backend = RecordingPowerBackend()
    pr = PowerRequest(reason="test", backend=backend)
    state = SessionState(idle_timeout_seconds=0.1, power_request=pr)
    stop = asyncio.Event()
    task = asyncio.create_task(state.run_idle_loop(stop))
    state.ping("first")
    # Re-ping just before the deadline so it should remain active.
    await asyncio.sleep(0.05)
    state.ping("refresh")
    await asyncio.sleep(0.08)  # 0.05 + 0.08 = 0.13 from first, 0.08 from refresh
    assert state.is_active is True
    stop.set()
    await task
