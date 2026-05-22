"""Shared helpers for tool implementations: result formatting, truncation,
path safety checks."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

OUTPUT_TRUNCATE_AT = 1500


def truncate_output(text: str, limit: int = OUTPUT_TRUNCATE_AT) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit] + f"\n[... truncated, {omitted} more chars ...]"


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
