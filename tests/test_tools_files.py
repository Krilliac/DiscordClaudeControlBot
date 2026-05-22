from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from discord_claude_control.config import ToolsConfig
from discord_claude_control.tools.files import build_file_tools


def _tools_config(restrict_paths: bool = False, allow_roots: tuple[str, ...] = ()) -> ToolsConfig:
    return ToolsConfig(
        input_auth_mode="autonomous",
        restrict_paths=restrict_paths,
        allow_roots=allow_roots,
        enabled=(),
        output_truncate_at=1500,
        powershell_default_timeout_s=30,
    )


async def _call(tool_obj: Any, args: dict[str, Any]) -> Any:
    """Invoke an SdkMcpTool's underlying handler."""
    return await tool_obj.handler(args)


@pytest.mark.asyncio
async def test_read_file_returns_contents(tmp_path: Path) -> None:
    p = tmp_path / "hello.txt"
    p.write_text("hello world", encoding="utf-8")
    tools = build_file_tools(_tools_config())
    result = await _call(tools["read_file"], {"path": str(p)})
    assert result["content"][0]["text"] == "hello world"
    assert "is_error" not in result


@pytest.mark.asyncio
async def test_read_file_missing_returns_error(tmp_path: Path) -> None:
    tools = build_file_tools(_tools_config())
    result = await _call(tools["read_file"], {"path": str(tmp_path / "nope.txt")})
    assert result.get("is_error") is True
    assert "does not exist" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_read_file_rejects_directory(tmp_path: Path) -> None:
    tools = build_file_tools(_tools_config())
    result = await _call(tools["read_file"], {"path": str(tmp_path)})
    assert result.get("is_error") is True
    assert "not a file" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_read_file_truncates_long_content(tmp_path: Path) -> None:
    p = tmp_path / "big.txt"
    p.write_text("x" * 5000, encoding="utf-8")
    tools = build_file_tools(_tools_config())
    result = await _call(tools["read_file"], {"path": str(p)})
    assert "truncated" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_read_file_binary_returns_hex_preview(tmp_path: Path) -> None:
    p = tmp_path / "bin.dat"
    p.write_bytes(b"\xff\x00\xfe\x01\x80nope")
    tools = build_file_tools(_tools_config())
    result = await _call(tools["read_file"], {"path": str(p)})
    body = result["content"][0]["text"]
    assert "binary" in body
    assert "hex" in body


@pytest.mark.asyncio
async def test_read_file_rejects_empty_path(tmp_path: Path) -> None:
    tools = build_file_tools(_tools_config())
    result = await _call(tools["read_file"], {"path": ""})
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_read_file_path_restriction_blocks_outside(tmp_path: Path) -> None:
    inside_root = tmp_path / "allowed"
    inside_root.mkdir()
    outside = tmp_path / "elsewhere" / "secret.txt"
    outside.parent.mkdir()
    outside.write_text("secret", encoding="utf-8")
    tools = build_file_tools(_tools_config(restrict_paths=True, allow_roots=(str(inside_root),)))
    result = await _call(tools["read_file"], {"path": str(outside)})
    assert result.get("is_error") is True
    assert "outside allow_roots" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_write_file_overwrite(tmp_path: Path) -> None:
    p = tmp_path / "out.txt"
    p.write_text("old", encoding="utf-8")
    tools = build_file_tools(_tools_config())
    result = await _call(
        tools["write_file"], {"path": str(p), "content": "new", "mode": "overwrite"}
    )
    assert "is_error" not in result
    assert p.read_text(encoding="utf-8") == "new"


@pytest.mark.asyncio
async def test_write_file_append(tmp_path: Path) -> None:
    p = tmp_path / "out.txt"
    p.write_text("hello ", encoding="utf-8")
    tools = build_file_tools(_tools_config())
    await _call(tools["write_file"], {"path": str(p), "content": "world", "mode": "append"})
    assert p.read_text(encoding="utf-8") == "hello world"


@pytest.mark.asyncio
async def test_write_file_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "nested" / "deeper" / "file.txt"
    tools = build_file_tools(_tools_config())
    await _call(tools["write_file"], {"path": str(p), "content": "hi"})
    assert p.read_text(encoding="utf-8") == "hi"


@pytest.mark.asyncio
async def test_write_file_invalid_mode(tmp_path: Path) -> None:
    tools = build_file_tools(_tools_config())
    p = tmp_path / "x.txt"
    result = await _call(tools["write_file"], {"path": str(p), "content": "x", "mode": "yolo"})
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_list_dir_returns_entries(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("aaa", encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "c.bin").write_bytes(b"hello")
    tools = build_file_tools(_tools_config())
    result = await _call(tools["list_dir"], {"path": str(tmp_path)})
    body = result["content"][0]["text"]
    assert "a.txt" in body
    assert "b" in body
    assert "<DIR>" in body
    assert "c.bin" in body


@pytest.mark.asyncio
async def test_list_dir_on_file_errors(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_text("x", encoding="utf-8")
    tools = build_file_tools(_tools_config())
    result = await _call(tools["list_dir"], {"path": str(f)})
    assert result.get("is_error") is True
    assert "not a directory" in result["content"][0]["text"]
