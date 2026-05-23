"""Tests for the reaction-based confirmation flow and tool wrapping."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from discord_claude_control.tools import (
    DESTRUCTIVE_TOOLS,
    INPUT_TOOLS,
    _confirm_wrap,
    _needs_confirm,
)
from discord_claude_control.tools import _context
from discord_claude_control.tools.confirm import (
    NO_EMOJI,
    YES_EMOJI,
    describe_action,
    request_confirmation,
)


# --------------------------------------------------------------------- #
# _needs_confirm matrix                                                 #
# --------------------------------------------------------------------- #


def test_needs_confirm_autonomous_never() -> None:
    for name in ("kill_process", "launch_app", "click", "type_text", "read_file"):
        assert not _needs_confirm(name, "autonomous")


def test_needs_confirm_destructive_gates_only_destructive_tools() -> None:
    assert _needs_confirm("kill_process", "confirm_destructive")
    assert _needs_confirm("launch_app", "confirm_destructive")
    for name in INPUT_TOOLS:
        assert not _needs_confirm(name, "confirm_destructive")
    assert not _needs_confirm("read_file", "confirm_destructive")


def test_needs_confirm_all_gates_input_and_destructive() -> None:
    for name in DESTRUCTIVE_TOOLS | INPUT_TOOLS:
        assert _needs_confirm(name, "confirm_all"), name
    # Non-destructive, non-input tools are always free to run.
    assert not _needs_confirm("read_file", "confirm_all")
    assert not _needs_confirm("screenshot", "confirm_all")


# --------------------------------------------------------------------- #
# describe_action                                                       #
# --------------------------------------------------------------------- #


def test_describe_action_kill_process() -> None:
    assert "kill_process" in describe_action("kill_process", {"name_or_pid": "1234"})
    assert "1234" in describe_action("kill_process", {"name_or_pid": "1234"})


def test_describe_action_launch_app_with_args() -> None:
    out = describe_action("launch_app", {"path": "notepad.exe", "args": ["x.txt"]})
    assert "notepad" in out
    assert "x.txt" in out


def test_describe_action_type_text_truncates_long() -> None:
    out = describe_action("type_text", {"text": "a" * 200})
    assert "..." in out
    assert len(out) < 200


# --------------------------------------------------------------------- #
# request_confirmation                                                  #
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_request_confirmation_no_context_default_denies() -> None:
    ch_token = _context.set_channel(None)
    _context.set_bot(None)
    try:
        result = await request_confirmation(user_id=1, action="x")
    finally:
        _context.reset_channel(ch_token)
    assert result is False


class _FakeMessage:
    def __init__(self) -> None:
        self.id = 999
        self.reactions: list[str] = []
        self.edits: list[str] = []

    async def add_reaction(self, emoji: str) -> None:
        self.reactions.append(emoji)

    async def edit(self, *, content: str) -> "_FakeMessage":
        self.edits.append(content)
        return self


class _FakeChannel:
    def __init__(self, msg: _FakeMessage) -> None:
        self._msg = msg
        self.sent: list[str] = []

    async def send(self, content: str = "", **_: Any) -> _FakeMessage:
        self.sent.append(content)
        return self._msg


class _FakeReaction:
    def __init__(self, emoji: str, msg: _FakeMessage) -> None:
        self.emoji = emoji
        self.message = msg


class _FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _FakeBot:
    def __init__(self, reaction: _FakeReaction | None, user: _FakeUser | None) -> None:
        self._reaction = reaction
        self._user = user

    async def wait_for(self, event: str, *, check: Any, timeout: float) -> tuple:
        assert event == "reaction_add"
        if self._reaction is None or self._user is None:
            raise asyncio.TimeoutError
        # Simulate Discord checking before delivering.
        assert check(self._reaction, self._user)
        return self._reaction, self._user


@pytest.mark.asyncio
async def test_request_confirmation_yes_returns_true() -> None:
    msg = _FakeMessage()
    channel = _FakeChannel(msg)
    user = _FakeUser(42)
    bot = _FakeBot(_FakeReaction(YES_EMOJI, msg), user)

    ch_token = _context.set_channel(channel)
    _context.set_bot(bot)
    try:
        result = await request_confirmation(user_id=42, action="kill_process('1')")
    finally:
        _context.reset_channel(ch_token)
        _context.set_bot(None)

    assert result is True
    assert YES_EMOJI in msg.reactions and NO_EMOJI in msg.reactions
    assert any("approved" in e for e in msg.edits)


@pytest.mark.asyncio
async def test_request_confirmation_no_returns_false() -> None:
    msg = _FakeMessage()
    channel = _FakeChannel(msg)
    user = _FakeUser(42)
    bot = _FakeBot(_FakeReaction(NO_EMOJI, msg), user)

    ch_token = _context.set_channel(channel)
    _context.set_bot(bot)
    try:
        result = await request_confirmation(user_id=42, action="kill_process('1')")
    finally:
        _context.reset_channel(ch_token)
        _context.set_bot(None)

    assert result is False
    assert any("denied" in e for e in msg.edits)


@pytest.mark.asyncio
async def test_request_confirmation_timeout_returns_false() -> None:
    msg = _FakeMessage()
    channel = _FakeChannel(msg)
    bot = _FakeBot(None, None)  # wait_for raises TimeoutError

    ch_token = _context.set_channel(channel)
    _context.set_bot(bot)
    try:
        result = await request_confirmation(user_id=42, action="x", timeout_s=0.1)
    finally:
        _context.reset_channel(ch_token)
        _context.set_bot(None)

    assert result is False
    assert any("timed out" in e for e in msg.edits)


# --------------------------------------------------------------------- #
# _confirm_wrap                                                         #
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_confirm_wrap_passthrough_when_autonomous() -> None:
    calls: list[dict[str, Any]] = []

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return {"content": [{"type": "text", "text": "ran"}]}

    wrapped = _confirm_wrap(
        "kill_process", handler, mode="autonomous", allowed_user_id=42
    )
    result = await wrapped({"name_or_pid": "1"})
    assert calls == [{"name_or_pid": "1"}]
    assert result["content"][0]["text"] == "ran"


@pytest.mark.asyncio
async def test_confirm_wrap_denies_when_no_context() -> None:
    calls: list[dict[str, Any]] = []

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return {"content": [{"type": "text", "text": "ran"}]}

    wrapped = _confirm_wrap(
        "kill_process", handler, mode="confirm_destructive", allowed_user_id=42
    )
    _context.set_bot(None)
    ch_token = _context.set_channel(None)
    try:
        result = await wrapped({"name_or_pid": "1"})
    finally:
        _context.reset_channel(ch_token)

    assert calls == []  # handler never ran
    assert result.get("is_error") is True
    assert "declined" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_confirm_wrap_runs_when_approved() -> None:
    calls: list[dict[str, Any]] = []

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return {"content": [{"type": "text", "text": "ran"}]}

    msg = _FakeMessage()
    channel = _FakeChannel(msg)
    user = _FakeUser(42)
    bot = _FakeBot(_FakeReaction(YES_EMOJI, msg), user)

    wrapped = _confirm_wrap(
        "kill_process", handler, mode="confirm_destructive", allowed_user_id=42
    )
    ch_token = _context.set_channel(channel)
    _context.set_bot(bot)
    try:
        result = await wrapped({"name_or_pid": "1"})
    finally:
        _context.reset_channel(ch_token)
        _context.set_bot(None)

    assert calls == [{"name_or_pid": "1"}]
    assert result["content"][0]["text"] == "ran"
