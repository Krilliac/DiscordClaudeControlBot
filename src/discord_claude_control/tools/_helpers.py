"""Shared helpers for tool implementations: result formatting, truncation,
path safety checks, large-output spill to Discord attachments."""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ._context import get_channel

log = logging.getLogger(__name__)

OUTPUT_TRUNCATE_AT = 1500


def truncate_output(text: str, limit: int = OUTPUT_TRUNCATE_AT) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit] + f"\n[... truncated, {omitted} more chars ...]"


_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(stem: str, ext: str = ".txt", *, max_len: int = 80) -> str:
    """Squash a free-form string into something Discord and most filesystems
    will accept as an attachment filename."""
    cleaned = _UNSAFE_FILENAME.sub("_", stem).strip("._-") or "output"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip("._-") or "output"
    if not ext.startswith("."):
        ext = "." + ext
    return cleaned + ext


async def spill_if_large(
    text: str,
    *,
    threshold: int,
    filename: str,
) -> str:
    """If `text` exceeds `threshold`, post the full bytes as a Discord
    attachment via the current channel context and return a truncated form
    annotated with a pointer to the attachment.

    Behaviour:
      - len(text) <= threshold        -> return text unchanged (no I/O)
      - over, no channel context     -> return truncate_output(text, threshold)
      - over, has channel            -> post attachment, return head + pointer

    The attachment is the OPERATOR's copy (full output, browsable in Discord).
    The returned string is the MODEL's copy (truncated, cheap to read back).
    """
    if len(text) <= threshold:
        return text

    channel = get_channel()
    if channel is None:
        return truncate_output(text, threshold)

    try:
        import discord

        buf = io.BytesIO(text.encode("utf-8", errors="replace"))
        await channel.send(
            content=f"full output ({len(text):,} bytes):",
            file=discord.File(buf, filename=filename),
        )
        return (
            truncate_output(text, threshold)
            + f"\n[full {len(text):,} bytes posted to chat as `{filename}`]"
        )
    except Exception:
        log.exception("spill_if_large: discord send failed")
        return truncate_output(text, threshold)


def text_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def error_result(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": f"ERROR: {message}"}],
        "is_error": True,
    }


def check_path_allowed(
    path: Path,
    *,
    restrict: bool,
    allow_roots: Iterable[str],
) -> None:
    """Raise PermissionError if `restrict` is True and `path` escapes allow_roots."""
    if not restrict:
        return
    resolved = path.resolve()
    for root in allow_roots:
        try:
            resolved.relative_to(Path(root).resolve())
            return
        except ValueError:
            continue
    raise PermissionError(f"path {path} is outside allow_roots {list(allow_roots)}")
