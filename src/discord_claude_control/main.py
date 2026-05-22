from __future__ import annotations

import argparse
import faulthandler
import logging
import sys
from pathlib import Path

from .bot import run_bot
from .config import ConfigError, load_config, load_secrets
from .health import consume_crash_marker, write_crash_marker

log = logging.getLogger(__name__)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _preflight(config_path: Path) -> int:
    """Validate config + secrets and exit. No Discord connect, no Anthropic call.

    Exit codes:
        0 -- everything looks good
        2 -- config.toml is missing or malformed
        3 -- secrets (env / .env) are missing or malformed
    """
    try:
        config = load_config(config_path)
    except FileNotFoundError as e:
        print(f"preflight: {e}", file=sys.stderr)
        return 2
    except ConfigError as e:
        print(f"preflight: config error: {e}", file=sys.stderr)
        return 2
    try:
        secrets = load_secrets()
    except ConfigError as e:
        print(f"preflight: secrets error: {e}", file=sys.stderr)
        return 3
    mode = "API mode (per-token)" if secrets.has_api_key else "subscription mode"
    print(
        "preflight OK\n"
        f"  config         : {config_path}\n"
        f"  model          : {config.agent.model}\n"
        f"  user_id        : {config.discord.allowed_user_id}\n"
        f"  channel_id     : {config.discord.allowed_channel_id}\n"
        f"  guild_id       : {config.discord.allowed_guild_id}\n"
        f"  auth mode      : {mode}\n"
        f"  idle timeout   : {config.session.idle_timeout_minutes} min\n"
        f"  attach enabled : {config.attach.enabled}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="discord-claude-control")
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate config + secrets and exit (no Discord connect, no Anthropic call)",
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if args.check:
        return _preflight(config_path)

    try:
        config = load_config(config_path)
    except (FileNotFoundError, ConfigError) as e:
        # Logging isn't configured yet; go straight to stderr so Task Scheduler
        # captures it in logs/stderr.log.
        print(f"startup: {e}", file=sys.stderr)
        return 2

    _setup_logging(config.logging.level)

    # Catch C-level crashes (e.g. ctypes/kernel32 segfaults) into stderr so
    # the scheduled task's redirected logs still see them.
    faulthandler.enable()

    # Surface any crash from the previous run, then rotate the marker so we
    # don't keep re-flagging the same crash on every restart.
    prior = consume_crash_marker()
    if prior is not None:
        log.warning(
            "previous run terminated with %s: %s",
            prior.get("type", "Unknown"),
            prior.get("message", ""),
        )

    try:
        secrets = load_secrets()
    except ConfigError as e:
        log.error("secrets: %s", e)
        return 3

    try:
        run_bot(config, secrets)
    except KeyboardInterrupt:
        log.info("interrupted; exiting cleanly")
        return 130
    except SystemExit:
        raise
    except BaseException as e:
        log.exception("unhandled exception in run_bot; writing crash marker")
        try:
            write_crash_marker(e)
        except Exception:
            log.exception("failed to write crash marker")
        raise
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
