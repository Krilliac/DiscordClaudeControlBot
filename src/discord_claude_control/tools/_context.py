"""
Channel routing for tools so they can post side-effects (screenshots, etc.)
back to the Discord channel that triggered the agent turn.

Originally implemented with ContextVar, but Python contextvars don't
propagate across the claude-agent-sdk subprocess+reader-loop boundary:
tools invoked by the SDK in response to claude.exe stdout run in the SDK
reader task's Context, which was created during agent.connect() at
startup and has no channel set. Since AgentBroker enforces at-most-one
turn in flight, a plain module-level slot is both simpler and correct.
"""

from __future__ import annotations

from typing import Any

_current_channel: Any = None


def set_channel(channel: Any) -> Any:
    """Set the active channel; return the previous value as a restore token."""
    global _current_channel
    prev = _current_channel
    _current_channel = channel
    return prev


def reset_channel(token: Any) -> None:
    """Restore the previous channel value (token returned by set_channel)."""
    global _current_channel
    _current_channel = token


def get_channel() -> Any:
    return _current_channel
