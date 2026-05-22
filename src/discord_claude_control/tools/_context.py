"""
ContextVar threading so tools can post side-effects (screenshots, etc.)
back to the Discord channel that triggered the agent turn.

The bot sets the current channel via `set_channel()` before calling
agent.submit(); tools that need to render artifacts call `get_channel()`.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

_current_channel: ContextVar[Any] = ContextVar("dcc_current_channel", default=None)


def set_channel(channel: Any) -> Token[Any]:
    return _current_channel.set(channel)


def reset_channel(token: Token[Any]) -> None:
    _current_channel.reset(token)


def get_channel() -> Any:
    return _current_channel.get()
