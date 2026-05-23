"""
Operational health primitives: liveness heartbeat and crash marker.

Heartbeat
---------
The bot writes the current epoch seconds to a small file at a steady
interval. An external watchdog (or ``task_status.ps1``) can compare the
file's age to a threshold and decide the bot is wedged. This catches
the "process is alive but the event loop is stuck" failure mode that
Task Scheduler's restart-on-exit does not.

Writes go through a ``*.tmp`` + ``os.replace`` so a reader never sees
a half-written file.

Crash marker
------------
On unhandled exit the entry point writes a one-line JSON record with
timestamp, exception type, and traceback. On the next startup the
marker is consumed, logged at WARNING level, and rotated to
``crash-marker.json.last`` so subsequent runs don't keep flagging the
same crash.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Defaults are deliberately repo-relative; the bot runs with the repo root
# as working directory under Task Scheduler / NSSM, so these resolve next
# to the existing logs/ directory.
DEFAULT_HEARTBEAT_PATH = Path("logs") / "heartbeat"
DEFAULT_CRASH_MARKER_PATH = Path("logs") / "crash-marker.json"
DEFAULT_HEARTBEAT_INTERVAL_S = 30.0


def write_heartbeat(
    path: Path = DEFAULT_HEARTBEAT_PATH,
    *,
    clock: Callable[[], float] = time.time,
) -> None:
    """Write the current epoch seconds to ``path``.

    The write is performed via a sibling ``.tmp`` file and an atomic
    ``os.replace`` so readers never observe a partial write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(f"{clock():.3f}\n", encoding="utf-8")
    os.replace(tmp, path)


async def run_heartbeat_loop(
    stop_event: asyncio.Event,
    path: Path = DEFAULT_HEARTBEAT_PATH,
    interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
) -> None:
    """Touch ``path`` every ``interval_s`` seconds until ``stop_event`` is set.

    A failure to write the heartbeat is logged but does not stop the loop;
    the next interval will try again. The loop exits promptly when
    ``stop_event`` is set, even mid-wait.
    """
    while not stop_event.is_set():
        try:
            write_heartbeat(path)
        except Exception:  # pragma: no cover - logging-only fallback
            log.exception("heartbeat write failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except TimeoutError:
            continue


def write_crash_marker(
    exc: BaseException,
    path: Path = DEFAULT_CRASH_MARKER_PATH,
    *,
    clock: Callable[[], float] = time.time,
) -> None:
    """Persist a JSON record describing an unhandled exit.

    The format is intentionally a single JSON object (not JSONL) so it is
    obvious there is at most one outstanding crash to investigate.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": clock(),
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(exc)),
    }
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def consume_crash_marker(path: Path = DEFAULT_CRASH_MARKER_PATH) -> dict[str, Any] | None:
    """If ``path`` exists, read it, rotate it to ``<path>.last``, and return parsed dict.

    Returns ``None`` if no marker is present. Parse failures are logged and
    treated as no marker (the corrupt file is left alone for inspection).
    """
    if not path.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.exception("crash marker at %s is unparseable; ignoring", path)
        return None
    try:
        rotated = path.with_suffix(path.suffix + ".last")
        if rotated.exists():
            rotated.unlink()
        os.replace(path, rotated)
    except Exception:  # pragma: no cover - logging-only fallback
        log.exception("failed to rotate crash marker %s", path)
    return data


def format_status(
    *,
    started_at: float,
    agent_connected: bool,
    broker_busy: bool,
    attach_clients: int | None,
    session_active: bool,
    seconds_until_idle: float | None,
    now: float | None = None,
) -> str:
    """Render a single-message status report for the ``!status`` command.

    Pure function so it can be unit-tested without driving discord.py.
    """
    now = time.time() if now is None else now
    uptime_s = max(0.0, now - started_at)
    uptime = _humanize_seconds(uptime_s)
    parts = [
        f"uptime: {uptime}",
        f"agent: {'connected' if agent_connected else 'disconnected'}",
        f"broker: {'busy' if broker_busy else 'idle'}",
        f"session: {'active' if session_active else 'idle'}",
    ]
    if session_active and seconds_until_idle is not None:
        parts.append(f"idle in: {int(seconds_until_idle)}s")
    if attach_clients is not None:
        parts.append(f"attach clients: {attach_clients}")
    return " | ".join(parts)


def _humanize_seconds(secs: float) -> str:
    secs = int(secs)
    days, secs = divmod(secs, 86_400)
    hours, secs = divmod(secs, 3_600)
    minutes, secs = divmod(secs, 60)
    if days:
        return f"{days}d{hours}h{minutes}m"
    if hours:
        return f"{hours}h{minutes}m{secs}s"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"
