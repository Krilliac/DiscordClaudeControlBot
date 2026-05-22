"""
Tool-invocation audit log. One JSON line per tool call with timestamp,
tool name, summarized args, error flag, and a truncated result summary.

The bot calls `setup_audit_logger(path)` once on startup; each tool's
handler is wrapped by `audit_wrap()` (in tools/__init__.py) so every
invocation is recorded. Rotation kicks in at 5 MB with 5 keep-arounds.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

_LOGGER_NAME = "discord_claude_control.audit"


def setup_audit_logger(
    path: Path | str,
    *,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(_LOGGER_NAME)
    already = any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        and Path(getattr(h, "baseFilename", "")).resolve() == target.resolve()
        for h in logger.handlers
    )
    if not already:
        handler = logging.handlers.RotatingFileHandler(
            target,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_invocation(
    tool_name: str,
    args: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    entry = {
        "ts": round(time.time(), 3),
        "tool": tool_name,
        "args": _summarize_args(args),
        "is_error": bool(result.get("is_error", False)),
        "result": _summarize_result(result),
    }
    logging.getLogger(_LOGGER_NAME).info(json.dumps(entry, default=str, ensure_ascii=False))


def _summarize_args(args: Mapping[str, Any]) -> dict[str, Any]:
    summarized: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > 200:
            summarized[key] = value[:200] + f"... ({len(value) - 200} more chars)"
        else:
            summarized[key] = value
    return summarized


def _summarize_result(result: Mapping[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text", ""))
                return text[:500]
    return ""


def audit_wrap(
    tool_name: str,
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def wrapped(args: dict[str, Any]) -> dict[str, Any]:
        result = await handler(args)
        try:
            log_invocation(tool_name, args, result)
        except Exception:
            logging.getLogger(__name__).exception("audit log write failed")
        return result

    return wrapped
