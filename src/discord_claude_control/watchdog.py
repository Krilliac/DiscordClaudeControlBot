"""
External watchdog: detect a wedged bot and restart it.

How it works
------------
The bot writes `logs/heartbeat` every ~30 s (see health.py). This module
runs as a SECOND, INDEPENDENT process (own Task Scheduler entry) that:

  1. Reads the heartbeat file's age every `--check-interval` seconds.
  2. If the age exceeds `--stale-seconds`, POSTs an alert to the configured
     Discord webhook AND invokes `task_restart.ps1` to bring the bot back.
  3. Suppresses repeated alerts during a cooldown window so a long outage
     doesn't spam the alerts channel.
  4. On the next fresh heartbeat after an alert, posts a recovery message.

Why a webhook, not the bot's token
----------------------------------
The webhook is a separate credential. If the bot's gateway token is reset
or revoked (this has happened in practice), the bot cannot log in -- but
the webhook still works, so the alert lands. Two independent failure
domains ⇒ two independent credentials.

Stdlib only (almost)
--------------------
The core (read_heartbeat_age, post_alert, run_watchdog) imports only
stdlib so it stays runnable even when the bot's venv is broken. The
webhook POST uses urllib.request; no `requests`, no aiohttp, no
discord.py. main() additionally tries `python-dotenv` to read .env,
but tolerates its absence (falls back to bare os.environ).

CLI
---
    python -m discord_claude_control.watchdog \
        [--heartbeat-path logs/heartbeat] \
        [--stale-seconds 180] \
        [--check-interval 60] \
        [--restart-script src/discord_claude_control/service/task_restart.ps1] \
        [--alert-cooldown 600]

Environment:
    ALERT_WEBHOOK_URL    -- Discord incoming webhook (required to run).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# Heartbeat reading                                                     #
# --------------------------------------------------------------------- #


def read_heartbeat_age(
    path: Path, *, now: float | None = None
) -> float | None:
    """Return seconds since the heartbeat was last written.

    Returns None if the file is missing or unparseable -- callers should
    treat both as "stale" since they both mean we have no proof of life.
    """
    if now is None:
        now = time.time()
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        log.exception("watchdog: heartbeat read failed (%s)", path)
        return None
    try:
        ts = float(raw)
    except ValueError:
        log.warning("watchdog: heartbeat content unparseable: %r", raw)
        return None
    return max(0.0, now - ts)


# --------------------------------------------------------------------- #
# Webhook alerts                                                        #
# --------------------------------------------------------------------- #


def post_alert(webhook_url: str, content: str, *, timeout_s: float = 10.0) -> bool:
    """POST a plain-text message to a Discord webhook. Returns True on
    HTTP 2xx, False on any error. Best-effort -- never raises."""
    if not webhook_url:
        log.warning("watchdog: no webhook URL; alert dropped: %s", content)
        return False
    body = json.dumps({"content": content[:1900]}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "dcc-watchdog/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            ok = 200 <= resp.status < 300
            if not ok:
                log.warning("watchdog: webhook HTTP %s", resp.status)
            return ok
    except urllib.error.URLError:
        log.exception("watchdog: webhook POST failed")
        return False
    except Exception:
        log.exception("watchdog: webhook POST raised unexpectedly")
        return False


# --------------------------------------------------------------------- #
# Restart                                                               #
# --------------------------------------------------------------------- #


def run_restart_script(
    script: Path, *, timeout_s: float = 60.0
) -> tuple[bool, str]:
    """Invoke task_restart.ps1 via PowerShell. Returns (ok, summary)."""
    if not script.exists():
        return False, f"restart script not found: {script}"
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, f"restart timed out after {timeout_s:.0f}s"
    except FileNotFoundError:
        return False, "powershell.exe not on PATH"
    except Exception as e:  # pragma: no cover - defensive
        log.exception("watchdog: restart subprocess raised")
        return False, f"restart subprocess raised: {e}"
    ok = proc.returncode == 0
    tail = (proc.stdout + "\n" + proc.stderr).strip().splitlines()[-5:]
    return ok, f"exit={proc.returncode}\n" + "\n".join(tail)


# --------------------------------------------------------------------- #
# State machine                                                         #
# --------------------------------------------------------------------- #


@dataclass
class WatchdogState:
    last_alert_ts: float = 0.0
    in_alert: bool = False


def evaluate(
    age: float | None,
    *,
    stale_threshold_s: float,
    cooldown_s: float,
    state: WatchdogState,
    now: float,
) -> str:
    """Decide what to do based on heartbeat age. Returns one of:

      "ok"        -- fresh; no action
      "alert"     -- stale; post alert + restart
      "suppress"  -- stale but in cooldown; log only
      "recover"  -- fresh after an alert; post recovery
    """
    if age is None or age > stale_threshold_s:
        if state.in_alert and (now - state.last_alert_ts) < cooldown_s:
            return "suppress"
        return "alert"
    if state.in_alert:
        return "recover"
    return "ok"


# --------------------------------------------------------------------- #
# Main loop                                                             #
# --------------------------------------------------------------------- #


def run_watchdog(
    *,
    heartbeat_path: Path,
    webhook_url: str,
    restart_script: Path | None,
    stale_threshold_s: float = 180.0,
    check_interval_s: float = 60.0,
    cooldown_s: float = 600.0,
    sleep: "callable" = time.sleep,  # type: ignore[valid-type]
    iterations: int | None = None,
) -> None:
    """Run the watchdog loop. `iterations=None` loops forever; tests pass an
    integer to bound the loop."""
    state = WatchdogState()
    n = 0
    while iterations is None or n < iterations:
        n += 1
        now = time.time()
        age = read_heartbeat_age(heartbeat_path, now=now)
        decision = evaluate(
            age,
            stale_threshold_s=stale_threshold_s,
            cooldown_s=cooldown_s,
            state=state,
            now=now,
        )
        age_str = f"{age:.1f}s" if age is not None else "missing"
        log.info("watchdog: heartbeat=%s decision=%s", age_str, decision)

        if decision == "alert":
            msg = (
                f":rotating_light: bot heartbeat stale "
                f"(age={age_str}, threshold={stale_threshold_s:.0f}s). "
                f"attempting restart."
            )
            post_alert(webhook_url, msg)
            if restart_script is not None:
                ok, summary = run_restart_script(restart_script)
                follow = ":white_check_mark: restart OK" if ok else ":x: restart FAILED"
                post_alert(webhook_url, f"{follow}\n```\n{summary[:1500]}\n```")
            state.in_alert = True
            state.last_alert_ts = now
        elif decision == "recover":
            post_alert(
                webhook_url,
                f":white_check_mark: bot recovered (heartbeat fresh: {age_str})",
            )
            state.in_alert = False

        try:
            sleep(check_interval_s)
        except KeyboardInterrupt:
            log.info("watchdog: KeyboardInterrupt; exiting")
            return


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    parser = argparse.ArgumentParser(prog="discord_claude_control.watchdog")
    parser.add_argument(
        "--heartbeat-path",
        type=Path,
        default=Path("logs/heartbeat"),
        help="path to the bot's heartbeat file (default: logs/heartbeat)",
    )
    parser.add_argument(
        "--stale-seconds",
        type=float,
        default=180.0,
        help="alert if heartbeat is older than this (default: 180)",
    )
    parser.add_argument(
        "--check-interval",
        type=float,
        default=60.0,
        help="seconds between heartbeat checks (default: 60)",
    )
    parser.add_argument(
        "--restart-script",
        type=Path,
        default=Path("src/discord_claude_control/service/task_restart.ps1"),
        help="PowerShell script invoked to restart the bot",
    )
    parser.add_argument(
        "--alert-cooldown",
        type=float,
        default=600.0,
        help="seconds before re-alerting on a continuing outage (default: 600)",
    )
    args = parser.parse_args(argv)

    # Best-effort .env load so the user can configure ALERT_WEBHOOK_URL in
    # the same .env file the bot reads. We tolerate python-dotenv being
    # absent so the watchdog still runs from a minimal Python install.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        log.info("python-dotenv not installed; reading env from os.environ only")

    webhook = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    if not webhook:
        sys.stderr.write(
            "ALERT_WEBHOOK_URL is not set. Create a Discord incoming webhook in\n"
            "your alerts channel and put the URL in .env or the environment.\n"
        )
        return 2

    log.info(
        "watchdog starting (heartbeat=%s, stale=%.0fs, interval=%.0fs, cooldown=%.0fs)",
        args.heartbeat_path,
        args.stale_seconds,
        args.check_interval,
        args.alert_cooldown,
    )
    try:
        run_watchdog(
            heartbeat_path=args.heartbeat_path,
            webhook_url=webhook,
            restart_script=args.restart_script,
            stale_threshold_s=args.stale_seconds,
            check_interval_s=args.check_interval,
            cooldown_s=args.alert_cooldown,
        )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
