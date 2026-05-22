"""File-system tools: read_file, write_file, list_dir.

Path restrictions are enforced via ToolsConfig.restrict_paths + allow_roots,
so the tool factory closes over the active config at registration time.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from ..config import ToolsConfig
from ._helpers import check_path_allowed, error_result, text_result, truncate_output

log = logging.getLogger(__name__)

_READ_DESC = (
    "Read a UTF-8 text file and return its contents. Returns the first "
    "`max_bytes` bytes (default 100,000) and truncates display at ~1500 "
    "chars. Binary files are returned as a hex prefix preview."
)

_WRITE_DESC = (
    "Write content to a file. `mode` is 'overwrite' (default) or 'append'. "
    "Parent directories are created as needed."
)

_LIST_DESC = (
    "List the immediate entries of a directory with sizes. Directories show "
    "<DIR> instead of a size."
)

_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "max_bytes": {"type": "integer", "default": 100_000},
    },
    "required": ["path"],
}

_WRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "content": {"type": "string"},
        "mode": {"type": "string", "enum": ["overwrite", "append"], "default": "overwrite"},
    },
    "required": ["path", "content"],
}

_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
}


def build_file_tools(config: ToolsConfig) -> dict[str, SdkMcpTool[Any]]:
    restrict = config.restrict_paths
    roots = config.allow_roots

    async def _read_file(args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path")
        max_bytes = args.get("max_bytes", 100_000)
        if not isinstance(path, str) or not path:
            return error_result("path must be a non-empty string")
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            return error_result("max_bytes must be a positive integer")
        p = Path(path)
        try:
            check_path_allowed(p, restrict=restrict, allow_roots=roots)
        except PermissionError as e:
            return error_result(str(e))
        if not p.exists():
            return error_result(f"path does not exist: {p}")
        if not p.is_file():
            return error_result(f"path is not a file: {p}")
        try:
            data = p.read_bytes()[:max_bytes]
        except OSError as e:
            return error_result(f"read failed: {e}")
        try:
            text = data.decode("utf-8")
            return text_result(truncate_output(text))
        except UnicodeDecodeError:
            preview = data[:200].hex()
            return text_result(f"<binary, {len(data)} bytes>\nhex(first 200): {preview}")

    async def _write_file(args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path")
        content = args.get("content", "")
        mode = args.get("mode", "overwrite")
        if not isinstance(path, str) or not path:
            return error_result("path must be a non-empty string")
        if not isinstance(content, str):
            return error_result("content must be a string")
        if mode not in ("overwrite", "append"):
            return error_result("mode must be 'overwrite' or 'append'")
        p = Path(path)
        try:
            check_path_allowed(p, restrict=restrict, allow_roots=roots)
        except PermissionError as e:
            return error_result(str(e))
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if mode == "overwrite":
                p.write_text(content, encoding="utf-8")
            else:
                with p.open("a", encoding="utf-8") as f:
                    f.write(content)
        except OSError as e:
            return error_result(f"write failed: {e}")
        return text_result(f"wrote {len(content)} chars to {p} (mode={mode})")

    async def _list_dir(args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return error_result("path must be a non-empty string")
        p = Path(path)
        try:
            check_path_allowed(p, restrict=restrict, allow_roots=roots)
        except PermissionError as e:
            return error_result(str(e))
        if not p.exists():
            return error_result(f"path does not exist: {p}")
        if not p.is_dir():
            return error_result(f"path is not a directory: {p}")
        try:
            lines: list[str] = []
            for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
                if child.is_dir():
                    lines.append(f"<DIR>       {child.name}")
                else:
                    try:
                        size = child.stat().st_size
                        lines.append(f"{size:>10}  {child.name}")
                    except OSError:
                        lines.append(f"     ?     {child.name}")
        except OSError as e:
            return error_result(f"list failed: {e}")
        return text_result(truncate_output("\n".join(lines)))

    return {
        "read_file": tool("read_file", _READ_DESC, _READ_SCHEMA)(_read_file),
        "write_file": tool("write_file", _WRITE_DESC, _WRITE_SCHEMA)(_write_file),
        "list_dir": tool("list_dir", _LIST_DESC, _LIST_SCHEMA)(_list_dir),
    }
