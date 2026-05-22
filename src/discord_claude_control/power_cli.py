"""
Manual smoke test for power.PowerRequest.

Acquires a system-required power request, sleeps, then releases. While
the script is sleeping, run `powercfg /requests` in another PowerShell
window; the named request should appear under SYSTEM and disappear once
this script exits.

    python -m discord_claude_control.power_cli --hold 30
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from .power import DEFAULT_REASON, PowerRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="discord-claude-control.power_cli")
    parser.add_argument(
        "--hold",
        type=float,
        default=15.0,
        help="seconds to hold the request before releasing (default: 15)",
    )
    parser.add_argument(
        "--reason",
        default=DEFAULT_REASON,
        help=f"reason string for `powercfg /requests` (default: {DEFAULT_REASON!r})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    pr = PowerRequest(reason=args.reason)
    print(f"using backend: {pr.backend_name}")
    pr.acquire()
    try:
        print("power request held.")
        print("verify with `powercfg /requests` in another shell.")
        print(f"holding for {args.hold:.1f}s ... (Ctrl-C to release early)")
        try:
            time.sleep(args.hold)
        except KeyboardInterrupt:
            print("\ninterrupted; releasing")
    finally:
        pr.release()
        print("released. `powercfg /requests` should no longer list it.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
