"""
Multiplexes input from multiple sources (Discord, attach clients) into a
single AgentSession, and fans every agent output event out to every
registered sink. One Claude conversation, many windows onto it.

Concurrency: at most one turn in flight at any time. Concurrent submits
are rejected; use `interrupt()` to cancel, then resubmit. The `is_busy`
property + the bool returned by `submit_nowait()` make this explicit.

Listeners:
- `add_activity_listener(fn)`: called as `fn(reason)` whenever a turn
  starts or ends. Used by the session state machine to ping the power
  request.
- `add_input_listener(fn)`: called as `fn(source, text)` for every
  submitted prompt. Used by the attach server to mirror Discord inputs
  to attached terminals.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Protocol

from .agent import ResponseSink, TurnResult


class _AgentLike(Protocol):
    async def submit(self, prompt: str, sink: ResponseSink) -> None: ...
    async def interrupt(self) -> None: ...


log = logging.getLogger(__name__)


class _FanoutSink:
    """Broadcasts each event to a snapshot of sinks. Exceptions per-sink
    are logged but do not break the others."""

    def __init__(self, sinks: list[ResponseSink]) -> None:
        self._sinks = sinks

    async def append(self, text: str) -> None:
        for sink in self._sinks:
            try:
                await sink.append(text)
            except Exception:
                log.exception("fanout: sink.append raised")

    async def commit(self, result: TurnResult) -> None:
        for sink in self._sinks:
            try:
                await sink.commit(result)
            except Exception:
                log.exception("fanout: sink.commit raised")

    async def fail(self, error: BaseException) -> None:
        for sink in self._sinks:
            try:
                await sink.fail(error)
            except Exception:
                log.exception("fanout: sink.fail raised")


class AgentBroker:
    def __init__(self, agent: _AgentLike) -> None:
        self._agent = agent
        self._sinks: set[ResponseSink] = set()
        self._inflight: asyncio.Task[None] | None = None
        self._activity_listeners: list[Callable[[str], None]] = []
        self._input_listeners: list[Callable[[str, str], None]] = []

    def add_sink(self, sink: ResponseSink) -> None:
        self._sinks.add(sink)

    def remove_sink(self, sink: ResponseSink) -> None:
        self._sinks.discard(sink)

    def add_activity_listener(self, fn: Callable[[str], None]) -> None:
        self._activity_listeners.append(fn)

    def add_input_listener(self, fn: Callable[[str, str], None]) -> None:
        self._input_listeners.append(fn)

    @property
    def is_busy(self) -> bool:
        return self._inflight is not None and not self._inflight.done()

    def submit_nowait(
        self,
        prompt: str,
        source_label: str,
        *,
        extra_sinks: Sequence[ResponseSink] = (),
    ) -> bool:
        """Start an agent turn without awaiting. Returns False if a turn is
        already in flight; the caller should surface that to its own UI."""
        if self.is_busy:
            log.info("rejected concurrent submit from %s", source_label)
            return False

        self._notify_input(source_label, prompt)
        self._notify_activity(f"input from {source_label}")

        self._inflight = asyncio.create_task(
            self._run_turn(prompt, source_label, list(extra_sinks))
        )
        return True

    async def interrupt(self) -> None:
        if self._inflight is None or self._inflight.done():
            return
        try:
            await self._agent.interrupt()
        except Exception:
            log.exception("agent.interrupt raised")
        self._inflight.cancel()

    @property
    def inflight(self) -> asyncio.Task[None] | None:
        return self._inflight

    async def _run_turn(
        self,
        prompt: str,
        source_label: str,
        extra_sinks: list[ResponseSink],
    ) -> None:
        fanout = _FanoutSink(list(self._sinks) + extra_sinks)
        try:
            await self._agent.submit(prompt, fanout)
        except asyncio.CancelledError:
            log.info("turn cancelled (%s)", source_label)
            raise
        except Exception:
            log.exception("agent turn crashed (%s)", source_label)
        finally:
            self._notify_activity(f"turn complete ({source_label})")

    def _notify_input(self, source: str, text: str) -> None:
        for fn in self._input_listeners:
            try:
                fn(source, text)
            except Exception:
                log.exception("input listener raised")

    def _notify_activity(self, reason: str) -> None:
        for fn in self._activity_listeners:
            try:
                fn(reason)
            except Exception:
                log.exception("activity listener raised")
