"""
State-machine tests for PowerRequest using a recording backend.

The real Windows ctypes integration is verified out-of-band by running
`python -m discord_claude_control.power_cli` on the actual PC and
watching `powercfg /requests` for the named entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from discord_claude_control.power import PowerRequest


@dataclass
class _Call:
    op: str
    handle: object | None = None
    reason: str | None = None


@dataclass
class RecordingBackend:
    name: str = "recording"
    calls: list[_Call] = field(default_factory=list)
    raise_on_set: bool = False
    raise_on_clear: bool = False
    _next_handle: int = 0

    def create(self, reason: str) -> object:
        self._next_handle += 1
        handle = self._next_handle
        self.calls.append(_Call("create", handle=handle, reason=reason))
        return handle

    def set_request(self, handle: object) -> None:
        self.calls.append(_Call("set_request", handle=handle))
        if self.raise_on_set:
            raise OSError("simulated set failure")

    def clear_request(self, handle: object) -> None:
        self.calls.append(_Call("clear_request", handle=handle))
        if self.raise_on_clear:
            raise OSError("simulated clear failure")

    def close(self, handle: object) -> None:
        self.calls.append(_Call("close", handle=handle))


def _ops(backend: RecordingBackend) -> list[str]:
    return [c.op for c in backend.calls]


def test_acquire_calls_create_then_set() -> None:
    b = RecordingBackend()
    pr = PowerRequest(reason="r", backend=b)
    pr.acquire()
    assert _ops(b) == ["create", "set_request"]
    assert b.calls[0].reason == "r"
    assert pr.held is True


def test_release_calls_clear_then_close() -> None:
    b = RecordingBackend()
    pr = PowerRequest(backend=b)
    pr.acquire()
    pr.release()
    assert _ops(b) == ["create", "set_request", "clear_request", "close"]
    assert pr.held is False


def test_acquire_is_idempotent() -> None:
    b = RecordingBackend()
    pr = PowerRequest(backend=b)
    pr.acquire()
    pr.acquire()
    pr.acquire()
    assert _ops(b) == ["create", "set_request"]


def test_release_without_acquire_is_a_noop() -> None:
    b = RecordingBackend()
    pr = PowerRequest(backend=b)
    pr.release()
    pr.release()
    assert b.calls == []
    assert pr.held is False


def test_double_release_is_a_noop_after_held() -> None:
    b = RecordingBackend()
    pr = PowerRequest(backend=b)
    pr.acquire()
    pr.release()
    pr.release()
    assert _ops(b) == ["create", "set_request", "clear_request", "close"]


def test_acquire_release_acquire_creates_fresh_handle() -> None:
    b = RecordingBackend()
    pr = PowerRequest(backend=b)
    pr.acquire()
    pr.release()
    pr.acquire()
    handles = [c.handle for c in b.calls if c.op == "create"]
    assert len(handles) == 2
    assert handles[0] != handles[1]


def test_set_failure_closes_handle_and_propagates() -> None:
    b = RecordingBackend(raise_on_set=True)
    pr = PowerRequest(backend=b)
    with pytest.raises(OSError, match="simulated set failure"):
        pr.acquire()
    assert _ops(b) == ["create", "set_request", "close"]
    assert pr.held is False


def test_clear_exception_still_closes_handle() -> None:
    b = RecordingBackend(raise_on_clear=True)
    pr = PowerRequest(backend=b)
    pr.acquire()
    with pytest.raises(OSError, match="simulated clear failure"):
        pr.release()
    # Even though clear_request raised, close must still have run via the finally
    # block, and the request must be considered no longer held so a subsequent
    # acquire() makes a fresh handle.
    assert _ops(b) == ["create", "set_request", "clear_request", "close"]
    assert pr.held is False


def test_context_manager_releases_on_normal_exit() -> None:
    b = RecordingBackend()
    pr = PowerRequest(backend=b)
    with pr as held:
        assert held is pr
        assert pr.held is True
    assert pr.held is False
    assert _ops(b) == ["create", "set_request", "clear_request", "close"]


def test_context_manager_releases_on_exception() -> None:
    b = RecordingBackend()
    pr = PowerRequest(backend=b)
    with pytest.raises(ValueError, match="boom"), pr:
        assert pr.held is True
        raise ValueError("boom")
    assert pr.held is False
    assert _ops(b) == ["create", "set_request", "clear_request", "close"]


def test_backend_name_is_exposed() -> None:
    b = RecordingBackend(name="recording-x")
    pr = PowerRequest(backend=b)
    assert pr.backend_name == "recording-x"


def test_default_reason_is_descriptive() -> None:
    from discord_claude_control.power import DEFAULT_REASON

    assert "discord-claude-control" in DEFAULT_REASON
    assert "active" in DEFAULT_REASON.lower()
