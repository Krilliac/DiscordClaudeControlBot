from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

import pytest

from discord_claude_control.agent import TurnResult
from discord_claude_control.broker import AgentBroker


@dataclass
class FakeAgent:
    """Stand-in for AgentSession: emits scripted text via the sink, then a
    success result. Has a hook to suspend mid-turn so tests can probe is_busy."""

    text_chunks: list[str] = field(default_factory=lambda: ["hello"])
    suspend_event: asyncio.Event | None = None
    interrupt_called: int = 0
    submitted: list[tuple[str, Any]] = field(default_factory=list)
    raise_on_submit: BaseException | None = None

    async def submit(self, prompt: str, sink: Any) -> None:
        self.submitted.append((prompt, sink))
        if self.raise_on_submit is not None:
            await sink.fail(self.raise_on_submit)
            raise self.raise_on_submit
        for t in self.text_chunks:
            await sink.append(t)
        if self.suspend_event is not None:
            await self.suspend_event.wait()
        await sink.commit(
            TurnResult(
                is_error=False,
                stop_reason="end_turn",
                duration_ms=1,
                session_id="s",
                total_cost_usd=0.0,
                num_turns=1,
            )
        )

    async def interrupt(self) -> None:
        self.interrupt_called += 1
        if self.suspend_event is not None:
            self.suspend_event.set()


class RecordingSink:
    def __init__(self, label: str = "?") -> None:
        self.label = label
        self.texts: list[str] = []
        self.committed: TurnResult | None = None
        self.failed: BaseException | None = None

    async def append(self, text: str) -> None:
        self.texts.append(text)

    async def commit(self, result: TurnResult) -> None:
        self.committed = result

    async def fail(self, error: BaseException) -> None:
        self.failed = error


async def _wait_done(broker: AgentBroker) -> None:
    if broker.inflight is not None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await broker.inflight


@pytest.mark.asyncio
async def test_submit_fans_out_to_registered_sinks() -> None:
    agent = FakeAgent(text_chunks=["hi ", "world"])
    broker = AgentBroker(agent)
    s1, s2 = RecordingSink("a"), RecordingSink("b")
    broker.add_sink(s1)
    broker.add_sink(s2)
    assert broker.submit_nowait("ping", "test") is True
    await _wait_done(broker)
    assert s1.texts == ["hi ", "world"]
    assert s2.texts == ["hi ", "world"]
    assert s1.committed is not None and s2.committed is not None


@pytest.mark.asyncio
async def test_extra_sinks_fan_out_only_for_that_turn() -> None:
    agent = FakeAgent(text_chunks=["x"])
    broker = AgentBroker(agent)
    persistent = RecordingSink("persistent")
    broker.add_sink(persistent)
    one_off = RecordingSink("one_off")
    broker.submit_nowait("a", "test", extra_sinks=[one_off])
    await _wait_done(broker)
    assert persistent.texts == ["x"]
    assert one_off.texts == ["x"]

    # Second turn without extras: persistent gets it, one_off does not.
    one_off.texts.clear()
    persistent.texts.clear()
    broker.submit_nowait("b", "test")
    await _wait_done(broker)
    assert persistent.texts == ["x"]
    assert one_off.texts == []


@pytest.mark.asyncio
async def test_busy_state_rejects_concurrent_submit() -> None:
    suspend = asyncio.Event()
    agent = FakeAgent(text_chunks=["first"], suspend_event=suspend)
    broker = AgentBroker(agent)
    assert broker.submit_nowait("a", "test") is True
    # Let the agent emit text, then it'll block on suspend.
    await asyncio.sleep(0.01)
    assert broker.is_busy is True
    assert broker.submit_nowait("b", "test") is False
    suspend.set()
    await _wait_done(broker)
    assert broker.is_busy is False


@pytest.mark.asyncio
async def test_interrupt_calls_agent_and_cancels_task() -> None:
    suspend = asyncio.Event()
    agent = FakeAgent(text_chunks=["first"], suspend_event=suspend)
    broker = AgentBroker(agent)
    broker.submit_nowait("a", "test")
    await asyncio.sleep(0.01)
    assert broker.is_busy is True
    await broker.interrupt()
    await asyncio.sleep(0.01)
    assert agent.interrupt_called == 1
    assert broker.is_busy is False


@pytest.mark.asyncio
async def test_interrupt_when_idle_is_noop() -> None:
    agent = FakeAgent()
    broker = AgentBroker(agent)
    await broker.interrupt()  # must not raise
    assert agent.interrupt_called == 0


@pytest.mark.asyncio
async def test_activity_listener_called_for_start_and_end() -> None:
    agent = FakeAgent(text_chunks=["x"])
    broker = AgentBroker(agent)
    reasons: list[str] = []
    broker.add_activity_listener(reasons.append)
    broker.submit_nowait("hi", "discord")
    await _wait_done(broker)
    assert any("input from discord" in r for r in reasons)
    assert any("turn complete (discord)" in r for r in reasons)


@pytest.mark.asyncio
async def test_input_listener_receives_source_and_text() -> None:
    agent = FakeAgent()
    broker = AgentBroker(agent)
    seen: list[tuple[str, str]] = []
    broker.add_input_listener(lambda src, txt: seen.append((src, txt)))
    broker.submit_nowait("hello", "attach")
    await _wait_done(broker)
    assert seen == [("attach", "hello")]


@pytest.mark.asyncio
async def test_sink_exception_does_not_break_others() -> None:
    class BadSink(RecordingSink):
        async def append(self, text: str) -> None:
            raise RuntimeError("boom")

    agent = FakeAgent(text_chunks=["x"])
    broker = AgentBroker(agent)
    bad = BadSink("bad")
    good = RecordingSink("good")
    broker.add_sink(bad)
    broker.add_sink(good)
    broker.submit_nowait("a", "test")
    await _wait_done(broker)
    assert good.texts == ["x"]


@pytest.mark.asyncio
async def test_remove_sink() -> None:
    agent = FakeAgent(text_chunks=["x"])
    broker = AgentBroker(agent)
    s = RecordingSink()
    broker.add_sink(s)
    broker.remove_sink(s)
    broker.submit_nowait("a", "test")
    await _wait_done(broker)
    assert s.texts == []


@pytest.mark.asyncio
async def test_agent_exception_still_calls_activity_complete() -> None:
    agent = FakeAgent(raise_on_submit=RuntimeError("kaboom"))
    broker = AgentBroker(agent)
    reasons: list[str] = []
    broker.add_activity_listener(reasons.append)
    broker.submit_nowait("a", "test")
    await _wait_done(broker)
    assert any("turn complete" in r for r in reasons)
