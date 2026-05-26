"""
Pre-sleep warning system. Token-free.

Background
----------
On a platform whose Modern Standby is "Network Disconnected" (the OEM-
locked common case), the Discord WebSocket goes dead the moment the
PC enters S0ix; no inbound message can wake the box. So the bot can't
react to a message that arrives during sleep -- only to messages that
arrive while the PC is awake.

This module makes that horizon visible to the operator. It watches the
OS sleep timer (powercfg STANDBYIDLE) and the last user input
(GetLastInputInfo), and posts plain ``channel.send()`` warnings at
configured thresholds before the projected sleep moment. The operator
can hit a single chat command (``!stay``) to ping the session,
re-acquire the bot's power request, and keep the PC awake without
spending any LLM tokens.

Cost model
----------
- Warning posts: zero. Direct ``channel.send`` -- no agent dispatch.
- ``!stay`` reply: zero. Intercepted in bot.on_message before agent path.
- Polling: ~1 powercfg subprocess + ~2 Win32 syscalls per tick (30s default).

Suspension behavior
-------------------
While the bot's session is active (i.e. PowerRequest held), the OS
sleep timer is paused by Windows. We detect that and suppress warnings.
When the session goes idle and releases the power request, Windows
resumes its normal idle countdown from the last user input event.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import re
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .session import SessionState

log = logging.getLogger(__name__)

DEFAULT_WARN_AT_SECONDS: tuple[int, ...] = (3600, 1800, 900, 300)  # 60m, 30m, 15m, 5m
DEFAULT_POLL_INTERVAL_S: float = 30.0


# --------------------------------------------------------------------------- #
# OS probes                                                                   #
# --------------------------------------------------------------------------- #


def get_sleep_timeout_s(on_battery: bool = False) -> int | None:
    """Return the active power scheme's STANDBYIDLE timeout in seconds.

    Returns ``None`` if discovery fails (powercfg missing, output unparseable).
    Returns ``0`` if the scheme is set to "Never sleep" on that line.
    """
    try:
        out = subprocess.run(
            ["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        log.debug("powercfg STANDBYIDLE query failed", exc_info=True)
        return None
    return parse_powercfg_standbyidle(out, on_battery=on_battery)


_RE_AC = re.compile(r"Current\s+AC\s+Power\s+Setting\s+Index:\s*0x([0-9a-fA-F]+)")
_RE_DC = re.compile(r"Current\s+DC\s+Power\s+Setting\s+Index:\s*0x([0-9a-fA-F]+)")


def parse_powercfg_standbyidle(output: str, *, on_battery: bool) -> int | None:
    """Pure parser for powercfg's STANDBYIDLE query output. Unit-testable."""
    pattern = _RE_DC if on_battery else _RE_AC
    match = pattern.search(output)
    if match is None:
        return None
    try:
        return int(match.group(1), 16)
    except ValueError:
        return None


def get_idle_seconds() -> float:
    """Seconds since the last keyboard / mouse event in the user session.

    Returns 0.0 on failure or on non-Windows. The bot is Windows-only in
    production; this is a defensive fallback.
    """
    if sys.platform != "win32":
        return 0.0
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return (_kernel32.GetTickCount() - info.dwTime) / 1000.0
    except Exception:  # pragma: no cover - defensive
        log.debug("GetLastInputInfo failed", exc_info=True)
        return 0.0


def is_on_battery() -> bool:
    """True if currently running on battery; False on AC or unknown.

    Returns False on non-Windows; the bot is Windows-only in production.
    """
    if sys.platform != "win32":
        return False
    try:
        status = _SYSTEM_POWER_STATUS()
        if not _kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return False
        # ACLineStatus: 0 = offline (battery), 1 = online (AC), 255 = unknown.
        return status.ACLineStatus == 0
    except Exception:  # pragma: no cover - defensive
        log.debug("GetSystemPowerStatus failed", exc_info=True)
        return False


# --------------------------------------------------------------------------- #
# Win32 plumbing (only loaded on Windows)                                     #
# --------------------------------------------------------------------------- #


if sys.platform == "win32":
    from ctypes import wintypes

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    class _SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_byte),
            ("BatteryFlag", ctypes.c_byte),
            ("BatteryLifePercent", ctypes.c_byte),
            ("SystemStatusFlag", ctypes.c_byte),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    _user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
else:  # pragma: no cover - non-Windows dev only

    class _LASTINPUTINFO:  # type: ignore[no-redef]
        cbSize = 0
        dwTime = 0

    class _SYSTEM_POWER_STATUS:  # type: ignore[no-redef]
        ACLineStatus = 1

    _user32 = None  # type: ignore[assignment]
    _kernel32 = None  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# Threshold logic                                                             #
# --------------------------------------------------------------------------- #


@dataclass
class _WarnCalcResult:
    """Outcome of a single calc tick. ``fired`` is the threshold to warn at,
    or None if no warning should fire this tick."""

    fired: int | None
    seconds_until_sleep: float | None
    suppression_reason: str | None


def calculate_warning(
    *,
    session_active: bool,
    on_battery: bool,
    warn_on_battery: bool,
    sleep_timeout_s: int | None,
    idle_seconds: float,
    thresholds: tuple[int, ...],
    already_warned: set[int],
) -> _WarnCalcResult:
    """Pure function that decides whether a tick should fire a warning.

    Suppressions, in order:
      1. session_active -> bot is holding PowerRequest; OS won't sleep.
      2. on_battery and not warn_on_battery -> respect battery preference.
      3. sleep_timeout_s is None -> discovery failed; do nothing this tick.
      4. sleep_timeout_s == 0 -> "Never sleep"; nothing to warn about.
      5. seconds_until_sleep <= 0 -> already past sleep time; clear and skip.

    Otherwise, pick the LARGEST threshold T such that:
       T <= seconds_until_sleep   AND   T not in already_warned.
    Firing the largest matching threshold matters when the bot is just
    starting up (e.g. sleep is 1h away and we should fire 60m first, not
    immediately spam 5m + 15m + 30m + 60m all at once).
    """
    if session_active:
        return _WarnCalcResult(None, None, "session active")
    if on_battery and not warn_on_battery:
        return _WarnCalcResult(None, None, "on battery, warn disabled")
    if sleep_timeout_s is None:
        return _WarnCalcResult(None, None, "sleep timeout unknown")
    if sleep_timeout_s == 0:
        return _WarnCalcResult(None, None, "sleep disabled (never)")
    seconds_until_sleep = sleep_timeout_s - idle_seconds
    if seconds_until_sleep <= 0:
        return _WarnCalcResult(None, 0.0, "past sleep time")

    # Among unfired thresholds that are >= seconds_until_sleep, fire the
    # LARGEST one that still applies. This gives "60m heads-up" instead of
    # "5m heads-up" when the bot first decides to warn.
    eligible = sorted(
        (t for t in thresholds if t not in already_warned and seconds_until_sleep <= t),
        reverse=True,
    )
    if not eligible:
        return _WarnCalcResult(None, seconds_until_sleep, "no eligible threshold")
    return _WarnCalcResult(eligible[0], seconds_until_sleep, None)


def humanize_seconds(secs: int) -> str:
    """Render an int-seconds duration as the largest sensible unit."""
    if secs >= 3600 and secs % 3600 == 0:
        return f"{secs // 3600}h"
    if secs >= 3600:
        m = (secs % 3600) // 60
        return f"{secs // 3600}h{m}m"
    if secs >= 60 and secs % 60 == 0:
        return f"{secs // 60}m"
    if secs >= 60:
        return f"{secs // 60}m{secs % 60}s"
    return f"{secs}s"


# --------------------------------------------------------------------------- #
# Async runner                                                                #
# --------------------------------------------------------------------------- #


# Type aliases for the test-friendly probe functions.
SleepTimeoutProbe = Callable[[bool], int | None]
IdleSecondsProbe = Callable[[], float]
BatteryProbe = Callable[[], bool]
ChannelNotifier = Callable[[str], Awaitable[None]]


@dataclass
class KeepAliveDeps:
    """Test seam for the runner. Production wires the real probes."""

    sleep_timeout: SleepTimeoutProbe = field(default=lambda on_bat: get_sleep_timeout_s(on_bat))
    idle_seconds: IdleSecondsProbe = field(default=get_idle_seconds)
    on_battery: BatteryProbe = field(default=is_on_battery)


class KeepAlive:
    """Polls the sleep ETA and posts pre-sleep warnings to a channel.

    The runner is async and bounded by ``stop_event``. Threshold state
    resets to empty every time the session becomes active (warnings
    silenced) and every time we observe past-sleep-time (idle counter
    rolled over after a wake).
    """

    def __init__(
        self,
        *,
        session: SessionState,
        notifier: ChannelNotifier,
        thresholds_seconds: tuple[int, ...] = DEFAULT_WARN_AT_SECONDS,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        warn_on_battery: bool = False,
        deps: KeepAliveDeps | None = None,
        stay_command: str = "!stay",
    ) -> None:
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be > 0")
        if not thresholds_seconds:
            raise ValueError("thresholds_seconds must be non-empty")
        for t in thresholds_seconds:
            if t <= 0:
                raise ValueError(f"threshold {t} must be > 0")
        self._session = session
        self._notifier = notifier
        self._thresholds: tuple[int, ...] = tuple(sorted(set(thresholds_seconds)))
        self._poll = poll_interval_s
        self._warn_on_battery = warn_on_battery
        self._deps = deps or KeepAliveDeps()
        self._warned: set[int] = set()
        self._stay_command = stay_command

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.tick()
            except Exception:  # pragma: no cover - logged and continue
                log.exception("keepalive tick raised; continuing")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll)
            except TimeoutError:
                continue

    async def tick(self) -> None:
        on_battery = self._deps.on_battery()
        sleep_timeout = self._deps.sleep_timeout(on_battery)
        idle_secs = self._deps.idle_seconds()
        result = calculate_warning(
            session_active=self._session.is_active,
            on_battery=on_battery,
            warn_on_battery=self._warn_on_battery,
            sleep_timeout_s=sleep_timeout,
            idle_seconds=idle_secs,
            thresholds=self._thresholds,
            already_warned=self._warned,
        )

        # Reset on resume / on becoming active. "past sleep time" usually
        # means the OS slept and the wake-up reset the idle counter.
        if result.suppression_reason in {"session active", "past sleep time"}:
            self._warned.clear()
            return
        if result.fired is None:
            return

        self._warned.add(result.fired)
        actual = int(result.seconds_until_sleep or 0)
        msg = format_warning(
            threshold_seconds=result.fired,
            seconds_until_sleep=actual,
            stay_command=self._stay_command,
        )
        try:
            await self._notifier(msg)
        except Exception:  # pragma: no cover - logged and continue
            log.exception("keepalive notifier raised")


def format_warning(*, threshold_seconds: int, seconds_until_sleep: int, stay_command: str) -> str:
    """Render the user-visible warning. Pure function so tests can assert it."""
    return (
        f":zzz: PC will sleep in ~{humanize_seconds(threshold_seconds)} "
        f"({humanize_seconds(seconds_until_sleep)} actual). "
        f"Send `{stay_command}` to hold it awake (no tokens)."
    )
