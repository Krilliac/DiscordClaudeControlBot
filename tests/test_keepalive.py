"""Tests for the pre-sleep warning module (keepalive.py).

All real Windows syscalls and the powercfg subprocess are stubbed via
KeepAliveDeps so the suite runs cross-platform.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from discord_claude_control.keepalive import (
    DEFAULT_WARN_AT_SECONDS,
    KeepAlive,
    KeepAliveDeps,
    calculate_warning,
    format_warning,
    humanize_seconds,
    parse_powercfg_standbyidle,
)


# ----- parse_powercfg_standbyidle -------------------------------------------


_SAMPLE_OUTPUT = """
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)
  Subgroup GUID: 238c9fa8-0aad-41ed-83f4-97be242c8f20  (Sleep)
    Power Setting GUID: 29f6c1db-86da-48c5-9fdb-f2b67b1f44da  (Sleep after)
      Minimum Possible Setting: 0x00000000
      Maximum Possible Setting: 0xffffffff
      Possible Settings increment: 0x00000001
      Possible Settings units: Seconds
  Current AC Power Setting Index: 0x00000e10
  Current DC Power Setting Index: 0x00000384
"""


def test_parse_powercfg_ac_value() -> None:
    # 0x00000e10 == 3600 seconds == 60 minutes
    assert parse_powercfg_standbyidle(_SAMPLE_OUTPUT, on_battery=False) == 3600


def test_parse_powercfg_dc_value() -> None:
    # 0x00000384 == 900 seconds == 15 minutes
    assert parse_powercfg_standbyidle(_SAMPLE_OUTPUT, on_battery=True) == 900


def test_parse_powercfg_unparseable_returns_none() -> None:
    assert parse_powercfg_standbyidle("this is not powercfg output", on_battery=False) is None


def test_parse_powercfg_partial_missing_dc_returns_none() -> None:
    only_ac = "Current AC Power Setting Index: 0x00000384\n"
    assert parse_powercfg_standbyidle(only_ac, on_battery=True) is None
    assert parse_powercfg_standbyidle(only_ac, on_battery=False) == 900


# ----- humanize_seconds -----------------------------------------------------


@pytest.mark.parametrize(
    "secs,expected",
    [
        (3600, "1h"),
        (7200, "2h"),
        (5400, "1h30m"),
        (1800, "30m"),
        (60, "1m"),
        (90, "1m30s"),
        (5, "5s"),
        (0, "0s"),
    ],
)
def test_humanize_seconds(secs: int, expected: str) -> None:
    assert humanize_seconds(secs) == expected


# ----- calculate_warning ----------------------------------------------------


def _fresh_args(**overrides):
    base = dict(
        session_active=False,
        on_battery=False,
        warn_on_battery=False,
        sleep_timeout_s=3600,
        idle_seconds=0.0,
        thresholds=(3600, 1800, 900, 300),
        already_warned=set(),
    )
    base.update(overrides)
    return base


def test_calc_fires_largest_eligible_threshold_at_startup() -> None:
    # Fresh bot, sleep timer 1h, no idle yet → 3600s until sleep → 60m fires
    result = calculate_warning(**_fresh_args())
    assert result.fired == 3600


def test_calc_fires_smaller_threshold_after_larger_already_warned() -> None:
    # 60m already fired, now 25 min remain → 30m fires next (not 15m)
    result = calculate_warning(
        **_fresh_args(idle_seconds=3600 - 1500, already_warned={3600})
    )
    assert result.fired == 1800


def test_calc_session_active_suppresses() -> None:
    result = calculate_warning(**_fresh_args(session_active=True))
    assert result.fired is None
    assert result.suppression_reason == "session active"


def test_calc_battery_suppresses_by_default() -> None:
    result = calculate_warning(**_fresh_args(on_battery=True))
    assert result.fired is None
    assert "battery" in result.suppression_reason


def test_calc_battery_with_warn_on_battery_true_fires() -> None:
    result = calculate_warning(
        **_fresh_args(on_battery=True, warn_on_battery=True)
    )
    assert result.fired == 3600


def test_calc_unknown_sleep_timeout_does_nothing() -> None:
    result = calculate_warning(**_fresh_args(sleep_timeout_s=None))
    assert result.fired is None
    assert result.suppression_reason == "sleep timeout unknown"


def test_calc_never_sleep_does_nothing() -> None:
    result = calculate_warning(**_fresh_args(sleep_timeout_s=0))
    assert result.fired is None
    assert result.suppression_reason == "sleep disabled (never)"


def test_calc_past_sleep_time_clears_signal() -> None:
    # idle > sleep timer -- the OS already attempted sleep
    result = calculate_warning(**_fresh_args(idle_seconds=4000, sleep_timeout_s=3600))
    assert result.fired is None
    assert result.suppression_reason == "past sleep time"


def test_calc_no_eligible_threshold_returns_none() -> None:
    # 45 min remain; we already warned 60m AND 30m; 15m and 5m thresholds are
    # not yet eligible because seconds_until_sleep (2700) > both. Wait...
    # 2700 <= 1800 is False, 2700 <= 900 is False, 2700 <= 300 is False.
    # So no threshold matches "<=" -- nothing fires.
    result = calculate_warning(
        **_fresh_args(idle_seconds=900, already_warned={3600})
    )
    assert result.fired is None
    assert result.suppression_reason == "no eligible threshold"


# ----- format_warning -------------------------------------------------------


def test_format_warning_includes_stay_command() -> None:
    text = format_warning(
        threshold_seconds=300, seconds_until_sleep=287, stay_command="!stay"
    )
    assert "!stay" in text
    assert "5m" in text  # threshold
    assert "4m47s" in text  # actual seconds_until_sleep
    assert "no tokens" in text


# ----- KeepAlive runner -----------------------------------------------------


class _FakeSession:
    def __init__(self, active: bool = False) -> None:
        self.is_active = active


@dataclass
class _Notifier:
    sent: list[str] = field(default_factory=list)

    async def __call__(self, content: str) -> None:
        self.sent.append(content)


async def test_keepalive_fires_warning_for_imminent_sleep() -> None:
    session = _FakeSession(active=False)
    notifier = _Notifier()
    deps = KeepAliveDeps(
        sleep_timeout=lambda on_bat: 600,    # 10 min sleep timer
        idle_seconds=lambda: 100.0,           # 100s of idle → 500s remain
        on_battery=lambda: False,
    )
    ka = KeepAlive(
        session=session,
        notifier=notifier,
        thresholds_seconds=(600, 300),
        poll_interval_s=0.01,
        deps=deps,
        stay_command="!stay",
    )
    await ka.tick()
    # Only the 600s threshold should fire on the first eligible tick because
    # 500 <= 600. (300 <= 500 is also true; should pick the LARGER, 600.)
    assert len(notifier.sent) == 1
    assert "10m" in notifier.sent[0]


async def test_keepalive_session_active_clears_warned_set() -> None:
    session = _FakeSession(active=False)
    notifier = _Notifier()
    deps = KeepAliveDeps(
        sleep_timeout=lambda on_bat: 600,
        idle_seconds=lambda: 100.0,
        on_battery=lambda: False,
    )
    ka = KeepAlive(
        session=session,
        notifier=notifier,
        thresholds_seconds=(600, 300),
        poll_interval_s=0.01,
        deps=deps,
    )
    await ka.tick()  # fires 600
    await ka.tick()  # no further eligible threshold given idle=100
    assert len(notifier.sent) == 1

    # Now session goes active -- warned set should clear
    session.is_active = True
    await ka.tick()
    assert len(notifier.sent) == 1
    # Confirm we'd warn again from scratch when session goes idle.
    session.is_active = False
    await ka.tick()
    # 500s remaining, threshold set freshly empty, 600 fires again.
    assert len(notifier.sent) == 2


async def test_keepalive_run_loop_stops_on_event() -> None:
    session = _FakeSession(active=True)  # active suppresses all warnings
    notifier = _Notifier()
    deps = KeepAliveDeps(
        sleep_timeout=lambda on_bat: 3600,
        idle_seconds=lambda: 0.0,
        on_battery=lambda: False,
    )
    ka = KeepAlive(
        session=session,
        notifier=notifier,
        thresholds_seconds=(3600,),
        poll_interval_s=0.01,
        deps=deps,
    )
    stop = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        stop.set()

    await asyncio.gather(ka.run(stop), stop_soon())
    # No warnings, no exceptions, returned cleanly.
    assert notifier.sent == []


async def test_keepalive_invalid_poll_interval_rejected() -> None:
    with pytest.raises(ValueError, match="poll_interval"):
        KeepAlive(
            session=_FakeSession(),
            notifier=_Notifier(),
            poll_interval_s=0.0,
        )


async def test_keepalive_invalid_thresholds_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        KeepAlive(
            session=_FakeSession(),
            notifier=_Notifier(),
            thresholds_seconds=(),
        )
    with pytest.raises(ValueError, match=r"threshold -1 must be > 0"):
        KeepAlive(
            session=_FakeSession(),
            notifier=_Notifier(),
            thresholds_seconds=(-1, 300),
        )


def test_default_thresholds_are_60_30_15_5() -> None:
    # Sanity: the docstring promise.
    assert DEFAULT_WARN_AT_SECONDS == (3600, 1800, 900, 300)
