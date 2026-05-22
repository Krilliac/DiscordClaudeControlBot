"""
Smoke tests for tools whose real behavior depends on Windows runtime
(PowerShell, screenshot, pyautogui, kill_process). On non-Windows we
verify the predictable error paths -- argument validation and the
fallback when the platform dependency is missing.
"""

from __future__ import annotations

from typing import Any

import pytest

from discord_claude_control.tools.input import build_input_tools
from discord_claude_control.tools.process import build_process_tools
from discord_claude_control.tools.shell import build_shell_tool


async def _call(t: Any, args: dict[str, Any]) -> Any:
    return await t.handler(args)


@pytest.mark.asyncio
async def test_shell_rejects_empty_command() -> None:
    result = await _call(build_shell_tool(), {"command": ""})
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_shell_rejects_bad_timeout() -> None:
    result = await _call(build_shell_tool(), {"command": "echo hi", "timeout_s": -1})
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_input_tools_validate_args() -> None:
    tools = build_input_tools()
    # Non-int x
    r = await _call(tools["move_mouse"], {"x": "a", "y": 0})
    assert r.get("is_error") is True
    # Bad button
    r = await _call(tools["click"], {"button": "purple"})
    assert r.get("is_error") is True
    # Non-string text
    r = await _call(tools["type_text"], {"text": 123})
    assert r.get("is_error") is True
    # Empty key
    r = await _call(tools["press_key"], {"key": ""})
    assert r.get("is_error") is True


@pytest.mark.asyncio
async def test_process_launch_rejects_empty_path() -> None:
    tools = build_process_tools()
    result = await _call(tools["launch_app"], {"path": ""})
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_process_launch_rejects_non_string_args() -> None:
    tools = build_process_tools()
    result = await _call(tools["launch_app"], {"path": "/bin/true", "args": ["ok", 5]})
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_process_kill_rejects_empty_target() -> None:
    tools = build_process_tools()
    result = await _call(tools["kill_process"], {"name_or_pid": ""})
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_process_kill_unknown_pid() -> None:
    tools = build_process_tools()
    # PID 99999999 should not exist
    result = await _call(tools["kill_process"], {"name_or_pid": "99999999"})
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_process_kill_unknown_name() -> None:
    tools = build_process_tools()
    result = await _call(
        tools["kill_process"],
        {"name_or_pid": "this-process-name-does-not-exist-123abc"},
    )
    assert result.get("is_error") is True
