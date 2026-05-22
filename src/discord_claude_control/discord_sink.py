"""
Streams agent text into a Discord channel.

Discord caps messages at 2000 chars. We append to the most recently posted
message (edit in place) as text arrives, and roll over to a new message
when the buffer is about to overflow. Whitespace boundaries are preferred
for the split so words don't get cut in half.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .agent import TurnResult

if TYPE_CHECKING:  # pragma: no cover
    import discord

log = logging.getLogger(__name__)

DISCORD_HARD_LIMIT = 2000
DISCORD_ROLLOVER_AT = 1900


@runtime_checkable
class _SendableMessage(Protocol):
    """Minimum surface the sink needs from a posted Discord message."""

    async def edit(self, *, content: str) -> _SendableMessage: ...


@runtime_checkable
class _SendableChannel(Protocol):
    """Minimum surface the sink needs from a Discord channel."""

    async def send(self, content: str) -> _SendableMessage: ...


class DiscordResponseSink:
    def __init__(self, channel: _SendableChannel | discord.abc.Messageable) -> None:
        self._channel = channel
        self._current_msg: _SendableMessage | None = None
        self._current_text: str = ""

    async def append(self, text: str) -> None:
        if not text:
            return
        remaining = DISCORD_ROLLOVER_AT - len(self._current_text)
        if remaining <= 0:
            await self._rollover()
            remaining = DISCORD_ROLLOVER_AT
        if len(text) <= remaining:
            self._current_text += text
            await self._flush()
            return
        # Doesn't fit. Split at the last whitespace inside the budget so we
        # don't bisect a word; fall back to a hard cut if there's no space.
        cut = text.rfind(" ", 0, remaining)
        if cut <= 0:
            cut = remaining
        self._current_text += text[:cut]
        await self._flush()
        await self._rollover()
        await self.append(text[cut:].lstrip())

    async def commit(self, result: TurnResult) -> None:
        if self._current_text:
            await self._flush()
        if result.is_error:
            reason = result.stop_reason or "unknown error"
            await self._channel.send(f":warning: agent reported error: {reason}")

    async def fail(self, error: BaseException) -> None:
        # Step 5+ will spin up a thread with the full traceback. For now,
        # post a one-line summary so the user sees something went wrong.
        summary = f"{type(error).__name__}: {error}"[:1800]
        await self._channel.send(f":x: agent failed\n```\n{summary}\n```")

    async def _flush(self) -> None:
        text = self._current_text or "..."
        if self._current_msg is None:
            self._current_msg = await self._channel.send(text)
        else:
            await self._current_msg.edit(content=text)

    async def _rollover(self) -> None:
        self._current_msg = None
        self._current_text = ""
