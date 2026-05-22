from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from discord_claude_control.agent import TurnResult
from discord_claude_control.discord_sink import (
    DISCORD_HARD_LIMIT,
    DISCORD_ROLLOVER_AT,
    DiscordResponseSink,
)


@dataclass
class FakeMessage:
    content: str
    edits: list[str] = field(default_factory=list)

    async def edit(self, *, content: str) -> Any:
        self.edits.append(content)
        self.content = content
        return self


@dataclass
class FakeChannel:
    sends: list[FakeMessage] = field(default_factory=list)

    async def send(self, content: str) -> FakeMessage:
        msg = FakeMessage(content=content)
        self.sends.append(msg)
        return msg


def _success_result() -> TurnResult:
    return TurnResult(
        is_error=False,
        stop_reason="end_turn",
        duration_ms=1,
        session_id="x",
        total_cost_usd=0.0,
        num_turns=1,
    )


def _error_result(reason: str = "rate_limit") -> TurnResult:
    return TurnResult(
        is_error=True,
        stop_reason=reason,
        duration_ms=1,
        session_id="x",
        total_cost_usd=0.0,
        num_turns=1,
    )


@pytest.mark.asyncio
async def test_short_response_sends_single_message() -> None:
    ch = FakeChannel()
    sink = DiscordResponseSink(ch)
    await sink.append("hello")
    await sink.append(" world")
    await sink.commit(_success_result())
    assert len(ch.sends) == 1
    assert ch.sends[0].content == "hello world"


@pytest.mark.asyncio
async def test_appending_edits_in_place() -> None:
    ch = FakeChannel()
    sink = DiscordResponseSink(ch)
    await sink.append("part 1 ")
    await sink.append("part 2")
    await sink.commit(_success_result())
    msg = ch.sends[0]
    assert msg.content == "part 1 part 2"
    # First flush posts new; subsequent flushes edit.
    assert len(msg.edits) >= 1


@pytest.mark.asyncio
async def test_long_text_rolls_over_to_multiple_messages() -> None:
    ch = FakeChannel()
    sink = DiscordResponseSink(ch)
    chunk = "word " * 500  # 2500 chars with whitespace
    await sink.append(chunk)
    await sink.commit(_success_result())
    assert len(ch.sends) >= 2
    for msg in ch.sends:
        assert len(msg.content) <= DISCORD_HARD_LIMIT
    full = "".join(m.content for m in ch.sends)
    # Whitespace at boundaries may be lstripped; words must all still be present.
    assert full.replace(" ", "") == chunk.replace(" ", "")


@pytest.mark.asyncio
async def test_split_prefers_whitespace_boundary() -> None:
    ch = FakeChannel()
    sink = DiscordResponseSink(ch)
    prefix = "a" * (DISCORD_ROLLOVER_AT - 5)
    await sink.append(prefix + " bcdefg")
    await sink.commit(_success_result())
    assert len(ch.sends) == 2
    assert ch.sends[0].content == prefix
    assert ch.sends[1].content == "bcdefg"


@pytest.mark.asyncio
async def test_no_whitespace_hard_cut() -> None:
    ch = FakeChannel()
    sink = DiscordResponseSink(ch)
    text = "x" * (DISCORD_ROLLOVER_AT + 100)
    await sink.append(text)
    await sink.commit(_success_result())
    assert len(ch.sends) == 2
    assert len(ch.sends[0].content) == DISCORD_ROLLOVER_AT
    assert len(ch.sends[1].content) == 100


@pytest.mark.asyncio
async def test_commit_on_error_appends_warning() -> None:
    ch = FakeChannel()
    sink = DiscordResponseSink(ch)
    await sink.append("partial")
    await sink.commit(_error_result("rate_limit"))
    assert len(ch.sends) == 2
    assert "rate_limit" in ch.sends[1].content


@pytest.mark.asyncio
async def test_fail_sends_summary() -> None:
    ch = FakeChannel()
    sink = DiscordResponseSink(ch)
    await sink.fail(RuntimeError("kaboom"))
    assert len(ch.sends) == 1
    assert "RuntimeError" in ch.sends[0].content
    assert "kaboom" in ch.sends[0].content


@pytest.mark.asyncio
async def test_empty_append_is_noop() -> None:
    ch = FakeChannel()
    sink = DiscordResponseSink(ch)
    await sink.append("")
    await sink.commit(_success_result())
    assert ch.sends == []


@pytest.mark.asyncio
async def test_commit_without_content_does_not_send_empty() -> None:
    ch = FakeChannel()
    sink = DiscordResponseSink(ch)
    await sink.commit(_success_result())
    assert ch.sends == []
