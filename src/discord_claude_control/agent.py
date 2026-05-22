"""
Wraps `ClaudeSDKClient` with a Discord-friendly streaming interface.

A single `AgentSession` instance represents one ongoing conversation that
survives across service restarts by resuming the persisted SDK session id.
Each Discord message becomes one call to `submit()`; the response is
streamed into a `ResponseSink` (which `bot.py` implements as a Discord
channel sink that edits messages in place).

The `client_factory` argument is the injection point for unit tests: pass
a fake factory and the agent loop can be exercised without touching the
real SDK or the Anthropic API.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpSdkServerConfig,
    McpServerConfig,
    ResultMessage,
    SystemMessage,
    TextBlock,
)

from .config import AgentConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnResult:
    is_error: bool
    stop_reason: str | None
    duration_ms: int
    session_id: str
    total_cost_usd: float | None
    num_turns: int


class ResponseSink(Protocol):
    async def append(self, text: str) -> None: ...
    async def commit(self, result: TurnResult) -> None: ...
    async def fail(self, error: BaseException) -> None: ...


class _SessionStore(Protocol):
    def load(self) -> str | None: ...
    def save(self, session_id: str) -> None: ...


ClientFactory = Callable[[ClaudeAgentOptions], ClaudeSDKClient]


def _default_client_factory(options: ClaudeAgentOptions) -> ClaudeSDKClient:
    return ClaudeSDKClient(options=options)


class AgentSession:
    def __init__(
        self,
        config: AgentConfig,
        session_store: _SessionStore,
        system_prompt: str | None = None,
        mcp_server: McpSdkServerConfig | None = None,
        allowed_tools: list[str] | None = None,
        *,
        client_factory: ClientFactory = _default_client_factory,
    ) -> None:
        self._config = config
        self._store = session_store
        self._system_prompt = system_prompt
        self._mcp_server = mcp_server
        self._allowed_tools = list(allowed_tools) if allowed_tools else []
        self._client_factory = client_factory
        self._client: ClaudeSDKClient | None = None
        self._current_session_id: str | None = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    @property
    def session_id(self) -> str | None:
        return self._current_session_id

    async def connect(self) -> None:
        if self._client is not None:
            return
        resume = self._store.load()
        mcp_servers: dict[str, McpServerConfig] = (
            {"dcc": self._mcp_server} if self._mcp_server is not None else {}
        )
        options = ClaudeAgentOptions(
            model=self._config.model,
            system_prompt=self._system_prompt,
            resume=resume,
            max_turns=self._config.max_tool_calls_per_message,
            mcp_servers=mcp_servers,
            allowed_tools=self._allowed_tools,
        )
        client = self._client_factory(options)
        await client.connect()
        self._client = client
        self._current_session_id = resume
        log.info(
            "agent connected (resume=%s, model=%s)",
            resume or "<new>",
            self._config.model,
        )

    async def disconnect(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.disconnect()
        finally:
            self._client = None

    async def submit(self, prompt: str, sink: ResponseSink) -> None:
        if self._client is None:
            raise RuntimeError("agent not connected; call connect() first")
        await self._client.query(prompt)
        try:
            async for msg in self._client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text:
                            await sink.append(block.text)
                elif isinstance(msg, ResultMessage):
                    result = TurnResult(
                        is_error=msg.is_error,
                        stop_reason=msg.stop_reason,
                        duration_ms=msg.duration_ms,
                        session_id=msg.session_id,
                        total_cost_usd=msg.total_cost_usd,
                        num_turns=msg.num_turns,
                    )
                    self._current_session_id = msg.session_id
                    self._store.save(msg.session_id)
                    await sink.commit(result)
                    return
                elif isinstance(msg, SystemMessage):
                    # Not rendered to the user.
                    log.debug("system message: %r", msg)
        except Exception as e:
            log.exception("agent.submit failed")
            await sink.fail(e)
            raise

    async def interrupt(self) -> None:
        if self._client is not None:
            await self._client.interrupt()
