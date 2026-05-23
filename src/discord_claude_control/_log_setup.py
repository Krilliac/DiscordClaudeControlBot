"""
Redirect sys.stdout and sys.stderr to files when running headlessly.

When launched under `pythonw.exe` on Windows (GUI subsystem, no console),
`sys.stdout` and `sys.stderr` are None -- anything written to them would
crash with AttributeError, and the `logging` module silently drops every
record because its default stream is sys.stderr.

This module detects that case and points the streams at append-mode log
files so logging output, `print()`, and uncaught traceback messages land
somewhere readable. We use pythonw under Task Scheduler specifically so
the bot has no visible terminal window that a user could close by
accident -- which has happened, and which forcibly kills the task.

Also activates if `DCC_REDIRECT_LOGS=1` is set, so a regular `python.exe`
launcher can opt in explicitly (useful for tests).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def redirect_logs_to_files(
    stdout_path: Path,
    stderr_path: Path,
    *,
    force: bool | None = None,
) -> None:
    """Point sys.stdout / sys.stderr at the given paths (append-mode, UTF-8,
    line-buffered so tail -f works in real time).

    Activates when:
      - sys.stdout is None (pythonw.exe / windowless launch), OR
      - sys.stderr is None, OR
      - DCC_REDIRECT_LOGS=1 in env, OR
      - force=True.

    Returns silently if no redirection is needed (force is False/None and
    we have real console streams).
    """
    if force is None:
        force = os.environ.get("DCC_REDIRECT_LOGS") == "1"
    needs_redirect = force or sys.stdout is None or sys.stderr is None
    if not needs_redirect:
        return
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = open(stdout_path, "a", buffering=1, encoding="utf-8", errors="replace")
    sys.stderr = open(stderr_path, "a", buffering=1, encoding="utf-8", errors="replace")
