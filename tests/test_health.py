"""Tests for the health primitives: heartbeat, crash marker, status formatter."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from discord_claude_control.health import (
    consume_crash_marker,
    format_status,
    run_heartbeat_loop,
    write_crash_marker,
    write_heartbeat,
)


# ---- write_heartbeat ---------------------------------------------------------


def test_write_heartbeat_creates_parent(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "logs" / "heartbeat"
    assert not target.parent.exists()
    write_heartbeat(target, clock=lambda: 1234.5)
    assert target.exists()
    assert target.read_text(encoding="utf-8").strip() == "1234.500"


def test_write_heartbeat_atomic_via_tmp_rename(tmp_path: Path) -> None:
    target = tmp_path / "heartbeat"
    target.write_text("old", encoding="utf-8")
    write_heartbeat(target, clock=lambda: 999.0)
    # No stray .tmp left behind.
    assert not target.with_suffix(target.suffix + ".tmp").exists()
    assert target.read_text(encoding="utf-8").strip() == "999.000"


def test_write_heartbeat_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "heartbeat"
    write_heartbeat(target, clock=lambda: 1.0)
    write_heartbeat(target, clock=lambda: 2.0)
    assert target.read_text(encoding="utf-8").strip() == "2.000"


# ---- run_heartbeat_loop ------------------------------------------------------


async def test_heartbeat_loop_writes_then_stops(tmp_path: Path) -> None:
    target = tmp_path / "heartbeat"
    stop = asyncio.Event()

    async def stopper() -> None:
        # Let the loop write once, then signal stop.
        await asyncio.sleep(0.05)
        stop.set()

    await asyncio.gather(
        run_heartbeat_loop(stop, target, interval_s=0.01),
        stopper(),
    )
    assert target.exists()
    # The value should be a recent timestamp (within last few seconds).
    value = float(target.read_text(encoding="utf-8").strip())
    assert abs(value - time.time()) < 10.0


async def test_heartbeat_loop_exits_immediately_if_already_stopped(tmp_path: Path) -> None:
    target = tmp_path / "heartbeat"
    stop = asyncio.Event()
    stop.set()
    # Should return without ever writing.
    await asyncio.wait_for(
        run_heartbeat_loop(stop, target, interval_s=60.0),
        timeout=1.0,
    )
    assert not target.exists()


# ---- crash marker ------------------------------------------------------------


def test_crash_marker_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "crash-marker.json"
    try:
        raise RuntimeError("kaboom")
    except RuntimeError as e:
        write_crash_marker(e, target, clock=lambda: 42.0)

    assert target.exists()
    data = consume_crash_marker(target)
    assert data is not None
    assert data["type"] == "RuntimeError"
    assert data["message"] == "kaboom"
    assert "kaboom" in data["traceback"]
    assert data["ts"] == 42.0

    # File should now be rotated.
    assert not target.exists()
    assert target.with_suffix(target.suffix + ".last").exists()

    # Second consume returns None (rotated, no live marker).
    assert consume_crash_marker(target) is None


def test_crash_marker_missing_returns_none(tmp_path: Path) -> None:
    assert consume_crash_marker(tmp_path / "absent.json") is None


def test_crash_marker_replaces_previous_last(tmp_path: Path) -> None:
    target = tmp_path / "crash-marker.json"
    rotated = target.with_suffix(target.suffix + ".last")

    # Pre-existing .last from a much-earlier crash.
    rotated.write_text(json.dumps({"type": "Old", "message": "earlier", "ts": 1.0, "traceback": ""}), encoding="utf-8")

    try:
        raise ValueError("newer")
    except ValueError as e:
        write_crash_marker(e, target, clock=lambda: 100.0)
    data = consume_crash_marker(target)
    assert data is not None
    assert data["message"] == "newer"

    # .last should now hold the newer one, not the older.
    new_last = json.loads(rotated.read_text(encoding="utf-8"))
    assert new_last["message"] == "newer"


def test_crash_marker_corrupt_returns_none(tmp_path: Path) -> None:
    target = tmp_path / "crash-marker.json"
    target.write_text("{not valid json", encoding="utf-8")
    # Corrupt file: we log and return None, leaving the file in place for inspection.
    assert consume_crash_marker(target) is None
    assert target.exists()


# ---- format_status -----------------------------------------------------------


def test_format_status_idle_session() -> None:
    text = format_status(
        started_at=1000.0,
        agent_connected=True,
        broker_busy=False,
        attach_clients=0,
        session_active=False,
        seconds_until_idle=None,
        now=1000.0 + 65,
    )
    assert "uptime: 1m5s" in text
    assert "agent: connected" in text
    assert "broker: idle" in text
    assert "session: idle" in text
    assert "idle in:" not in text  # only present when active
    assert "attach clients: 0" in text


def test_format_status_active_with_countdown() -> None:
    text = format_status(
        started_at=0.0,
        agent_connected=True,
        broker_busy=True,
        attach_clients=2,
        session_active=True,
        seconds_until_idle=412.7,
        now=3600 + 1,
    )
    assert "uptime: 1h0m1s" in text
    assert "broker: busy" in text
    assert "session: active" in text
    assert "idle in: 412s" in text
    assert "attach clients: 2" in text


def test_format_status_disconnected_agent() -> None:
    text = format_status(
        started_at=0.0,
        agent_connected=False,
        broker_busy=False,
        attach_clients=None,
        session_active=False,
        seconds_until_idle=None,
        now=0.0,
    )
    assert "agent: disconnected" in text
    assert "attach clients" not in text  # omitted when attach is None


def test_format_status_days_uptime() -> None:
    text = format_status(
        started_at=0.0,
        agent_connected=True,
        broker_busy=False,
        attach_clients=None,
        session_active=False,
        seconds_until_idle=None,
        now=86400 * 3 + 3600 * 2 + 60 * 5,
    )
    assert "uptime: 3d2h5m" in text


# Reduce pytest-asyncio noise on the non-async tests.
pytestmark_for_unit = pytest.mark.parametrize("_", [None])
