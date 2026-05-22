"""
Tests for AgentSession using a scripted fake of ClaudeSDKClient.

The real SDK speaks to the Claude Code CLI under the hood and needs an
ANTHROPIC_API_KEY, so we never instantiate it. Instead, we inject a fake
client via the `client_factory` hook on AgentSession.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
)

from discord_claude_control.agent import AgentSession, TurnResult
from discord_claude_control.config import AgentConfig


@dataclass
class FakeStore:
    initial: str | None = None
    saved: list[str] = field(default_factory=list)

    def load(self) -> str | None:
        return self.initial

    def save(self, session_id: str) -> None:
        self.saved.append(session_id)


@dataclass
class FakeClient:
    options: ClaudeAgentOptions | None = None
    connected: bool = False
    disconnected: bool = False
    interrupted: bool = False
    queries: list[str] = field(default_factory=list)
    scripted: list[list[Any]] = field(default_factory=list)
    _idx: int = 0
    raise_during_receive: BaseException | None = None

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def interrupt(self) -> None:
        self.interrupted = True

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def receive_response(self) -> AsyncIterator[Any]:
        if self._idx >= len(self.scripted):
            return
        for msg in self.scripted[self._idx]:
            yield msg
            if self.raise_during_receive is not None:
                raise self.raise_during_receive
        self._idx += 1


class RecordingSink:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.committed: TurnResult | None = None
        self.failed: BaseException | None = None

    async def append(self, text: str) -> None:
        self.texts.append(text)

    async def commit(self, result: TurnResult) -> None:
        self.committed = result

    async def fail(self, error: BaseException) -> None:
        self.failed = error


def _agent_config() -> AgentConfig:
    return AgentConfig(
        model="claude-opus-4-7",
        max_tool_calls_per_message=20,
        conversation_db_path="conversation.db",
        system_prompt=None,
        system_prompt_path=None,
    )


def _result(
    *,
    session_id: str = "sess-1",
    is_error: bool = False,
    stop_reason: str | None = "end_turn",
) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=5,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        stop_reason=stop_reason,
        total_cost_usd=0.0,
    )


def _assistant(*texts: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextBlock(text=t) for t in texts],
        model="claude-opus-4-7",
    )


def _make_agent(
    client: FakeClient,
    store: FakeStore,
) -> AgentSession:
    def factory(opts: ClaudeAgentOptions) -> Any:
        client.options = opts
        return client

    return AgentSession(
        config=_agent_config(),
        session_store=store,
        client_factory=factory,
    )


@pytest.mark.asyncio
async def test_connect_passes_resume_from_store() -> None:
    store = FakeStore(initial="resume-me")
    client = FakeClient()
    agent = _make_agent(client, store)
    await agent.connect()
    assert client.connected is True
    assert client.options is not None
    assert client.options.resume == "resume-me"
    assert client.options.model == "claude-opus-4-7"
    assert agent.is_connected is True


@pytest.mark.asyncio
async def test_connect_no_resume_when_store_empty() -> None:
    store = FakeStore(initial=None)
    client = FakeClient()
    agent = _make_agent(client, store)
    await agent.connect()
    assert client.options is not None
    assert client.options.resume is None


@pytest.mark.asyncio
async def test_connect_is_idempotent() -> None:
    store = FakeStore()
    clients_built: list[FakeClient] = []

    def factory(_opts: ClaudeAgentOptions) -> Any:
        c = FakeClient()
        clients_built.append(c)
        return c

    agent = AgentSession(
        config=_agent_config(),
        session_store=store,
        client_factory=factory,
    )
    await agent.connect()
    await agent.connect()
    assert len(clients_built) == 1


@pytest.mark.asyncio
async def test_submit_streams_text_and_commits() -> None:
    store = FakeStore()
    client = FakeClient(
        scripted=[[_assistant("hello "), _assistant("world"), _result(session_id="new-sess")]]
    )
    agent = _make_agent(client, store)
    await agent.connect()
    sink = RecordingSink()
    await agent.submit("say hi", sink)
    assert client.queries == ["say hi"]
    assert sink.texts == ["hello ", "world"]
    assert sink.committed is not None
    assert sink.committed.session_id == "new-sess"
    assert sink.committed.is_error is False
    assert store.saved == ["new-sess"]
    assert agent.session_id == "new-sess"


@pytest.mark.asyncio
async def test_submit_ignores_system_messages() -> None:
    store = FakeStore()
    sysmsg = SystemMessage(subtype="init", data={})
    client = FakeClient(scripted=[[sysmsg, _assistant("ok"), _result()]])
    agent = _make_agent(client, store)
    await agent.connect()
    sink = RecordingSink()
    await agent.submit("hi", sink)
    assert sink.texts == ["ok"]
    assert sink.committed is not None


@pytest.mark.asyncio
async def test_submit_skips_empty_text_blocks() -> None:
    store = FakeStore()
    client = FakeClient(scripted=[[_assistant("", "real text", ""), _result()]])
    agent = _make_agent(client, store)
    await agent.connect()
    sink = RecordingSink()
    await agent.submit("hi", sink)
    assert sink.texts == ["real text"]


@pytest.mark.asyncio
async def test_submit_propagates_exception_and_calls_fail() -> None:
    store = FakeStore()
    client = FakeClient(scripted=[[_assistant("partial ")]])
    client.raise_during_receive = RuntimeError("boom")
    agent = _make_agent(client, store)
    await agent.connect()
    sink = RecordingSink()
    with pytest.raises(RuntimeError, match="boom"):
        await agent.submit("doomed", sink)
    assert sink.texts == ["partial "]
    assert sink.failed is not None
    assert isinstance(sink.failed, RuntimeError)
    assert sink.committed is None
    # Session id wasn't updated on failure.
    assert store.saved == []


@pytest.mark.asyncio
async def test_error_result_still_commits() -> None:
    store = FakeStore()
    client = FakeClient(
        scripted=[
            [_assistant("got rate limited"), _result(is_error=True, stop_reason="rate_limit")]
        ]
    )
    agent = _make_agent(client, store)
    await agent.connect()
    sink = RecordingSink()
    await agent.submit("hi", sink)
    assert sink.committed is not None
    assert sink.committed.is_error is True
    assert sink.committed.stop_reason == "rate_limit"


@pytest.mark.asyncio
async def test_submit_without_connect_raises() -> None:
    agent = AgentSession(
        config=_agent_config(),
        session_store=FakeStore(),
        client_factory=lambda _opts: FakeClient(),  # type: ignore[arg-type,return-value]
    )
    with pytest.raises(RuntimeError, match="not connected"):
        await agent.submit("hi", RecordingSink())


@pytest.mark.asyncio
async def test_interrupt_calls_client_interrupt() -> None:
    store = FakeStore()
    client = FakeClient()
    agent = _make_agent(client, store)
    await agent.connect()
    await agent.interrupt()
    assert client.interrupted is True


@pytest.mark.asyncio
async def test_interrupt_before_connect_is_noop() -> None:
    store = FakeStore()
    agent = _make_agent(FakeClient(), store)
    await agent.interrupt()  # must not raise


@pytest.mark.asyncio
async def test_disconnect_closes_client() -> None:
    store = FakeStore()
    client = FakeClient()
    agent = _make_agent(client, store)
    await agent.connect()
    await agent.disconnect()
    assert client.disconnected is True
    assert agent.is_connected is False
