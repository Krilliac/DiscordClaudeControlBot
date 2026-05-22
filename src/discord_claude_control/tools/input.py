"""Mouse and keyboard input tools (Windows-only at runtime via pyautogui).

pyautogui is imported lazily so the rest of the project still installs
and tests cleanly on non-Windows.
"""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool

from ._helpers import error_result, text_result

log = logging.getLogger(__name__)


def _import_pyautogui() -> Any | None:
    try:
        import pyautogui
    except Exception as e:
        log.warning("pyautogui unavailable: %s", e)
        return None
    return pyautogui


_MOVE_DESC = "Move the mouse pointer to absolute screen coordinates (x, y)."
_CLICK_DESC = (
    "Click a mouse button at the current pointer location. `button` is "
    "'left', 'right', or 'middle'."
)
_TYPE_DESC = "Type the given text via simulated keystrokes."
_PRESS_DESC = (
    "Press a single key by pyautogui name, e.g. 'enter', 'esc', 'win', "
    "'ctrl', 'tab', 'space', 'f5', 'pageup', etc."
)

_MOVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
    "required": ["x", "y"],
}
_CLICK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
    },
}
_TYPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}
_PRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"key": {"type": "string"}},
    "required": ["key"],
}


async def _move_mouse(args: dict[str, Any]) -> dict[str, Any]:
    pg = _import_pyautogui()
    if pg is None:
        return error_result("pyautogui not available (not on Windows or not installed)")
    x = args.get("x")
    y = args.get("y")
    if not isinstance(x, int) or isinstance(x, bool):
        return error_result("x must be an integer")
    if not isinstance(y, int) or isinstance(y, bool):
        return error_result("y must be an integer")
    try:
        pg.moveTo(x, y)
    except Exception as e:
        return error_result(f"moveTo failed: {e}")
    return text_result(f"mouse moved to ({x}, {y})")


async def _click(args: dict[str, Any]) -> dict[str, Any]:
    pg = _import_pyautogui()
    if pg is None:
        return error_result("pyautogui not available")
    button = args.get("button", "left")
    if button not in ("left", "right", "middle"):
        return error_result("button must be 'left', 'right', or 'middle'")
    try:
        pg.click(button=button)
    except Exception as e:
        return error_result(f"click failed: {e}")
    return text_result(f"clicked {button} mouse button")


async def _type_text(args: dict[str, Any]) -> dict[str, Any]:
    pg = _import_pyautogui()
    if pg is None:
        return error_result("pyautogui not available")
    text = args.get("text")
    if not isinstance(text, str):
        return error_result("text must be a string")
    try:
        pg.typewrite(text, interval=0.01)
    except Exception as e:
        return error_result(f"typewrite failed: {e}")
    return text_result(f"typed {len(text)} chars")


async def _press_key(args: dict[str, Any]) -> dict[str, Any]:
    pg = _import_pyautogui()
    if pg is None:
        return error_result("pyautogui not available")
    key = args.get("key")
    if not isinstance(key, str) or not key:
        return error_result("key must be a non-empty string")
    try:
        pg.press(key)
    except Exception as e:
        return error_result(f"press failed: {e}")
    return text_result(f"pressed {key}")


def build_input_tools() -> dict[str, SdkMcpTool[Any]]:
    return {
        "move_mouse": tool("move_mouse", _MOVE_DESC, _MOVE_SCHEMA)(_move_mouse),
        "click": tool("click", _CLICK_DESC, _CLICK_SCHEMA)(_click),
        "type_text": tool("type_text", _TYPE_DESC, _TYPE_SCHEMA)(_type_text),
        "press_key": tool("press_key", _PRESS_DESC, _PRESS_SCHEMA)(_press_key),
    }
