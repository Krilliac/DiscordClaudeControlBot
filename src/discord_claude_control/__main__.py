from __future__ import annotations

import sys
from pathlib import Path

# Redirect logs FIRST, before any other imports. Under pythonw.exe
# (windowless Task Scheduler launch) sys.stdout / sys.stderr are None
# and the logging module would silently drop every record. This call
# is a no-op under a normal console python.exe.
from ._log_setup import redirect_logs_to_files

redirect_logs_to_files(
    stdout_path=Path("logs") / "stdout.log",
    stderr_path=Path("logs") / "stderr.log",
)

from .main import main  # noqa: E402  -- intentional post-redirect import

if __name__ == "__main__":
    sys.exit(main())
