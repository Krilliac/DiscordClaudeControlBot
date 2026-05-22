from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .bot import run_bot
from .config import load_config, load_secrets


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="discord-claude-control")
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    args = parser.parse_args(argv)

    config = load_config(Path(args.config))
    _setup_logging(config.logging.level)
    secrets = load_secrets()
    run_bot(config, secrets)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
