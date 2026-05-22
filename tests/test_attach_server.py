"""
Integration tests for AttachServer. Opens a real loopback TCP socket so
the JSON wire protocol is exercised end-to-end against a real asyncio
StreamReader/Writer pair.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from discord_claude_control.attach_server import AttachServer
from discord_claude_control.broker import AgentBroker
from tests.test_broker import FakeAgent


async def _read_json_line(reader: asyncio.StreamReader, timeout: float = 1.0) -> Any:
    raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
    return json.loads(raw.decode("utf-8").strip())


async def _drain_until(
    reader: asyncio.StreamReader,
    predicate: Any,
    timeout: float = 1.0,
) -> Any:
    """Read messages until one matches predicate; return that one."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"predicate not matched within {timeout}s")
        msg = await _read_json_line(reader, timeout=remaining)
        if predicate(msg):
            return msg


async def _send(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
    writer.write((json.dumps(payload) + "\n").encode("utf-8"))
    await writer.drain()


@pytest.mark.asyncio
async def test_input_submits_to_broker_and_text_streams_back() -> None:
    agent = FakeAgent(text_chunks=["hello ", "world"])
    broker = AgentBroker(agent)
    server = AttachServer(broker, host="127.0.0.1", port=0)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        try:
            await _send(writer, {"type": "input", "text": "say hi"})
            echo = await _drain_until(reader, lambda m: m["type"] == "input_echo")
            assert echo["source"] == "attach"
            assert echo["text"] == "say hi"
            chunks = []
            while True:
                msg = await _read_json_line(reader, timeout=1.0)
                if msg["type"] == "text":
                    chunks.append(msg["text"])
                elif msg["type"] == "turn_done":
                    assert msg["is_error"] is False
                    break
            assert "".join(chunks) == "hello world"
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_invalid_json_returns_error() -> None:
    agent = FakeAgent()
    broker = AgentBroker(agent)
    server = AttachServer(broker, host="127.0.0.1", port=0)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        try:
            writer.write(b"not-json\n")
            await writer.drain()
            msg = await _drain_until(reader, lambda m: m["type"] == "error")
            assert "invalid json" in msg["message"].lower()
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_unknown_type_returns_error() -> None:
    agent = FakeAgent()
    broker = AgentBroker(agent)
    server = AttachServer(broker, host="127.0.0.1", port=0)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        try:
            await _send(writer, {"type": "frobnicate"})
            msg = await _drain_until(reader, lambda m: m["type"] == "error")
            assert "unknown type" in msg["message"]
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_ping_returns_pong() -> None:
    agent = FakeAgent()
    broker = AgentBroker(agent)
    server = AttachServer(broker, host="127.0.0.1", port=0)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        try:
            await _send(writer, {"type": "ping"})
            msg = await _drain_until(reader, lambda m: m["type"] == "pong")
            assert msg.get("did") is None
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_multiple_clients_see_each_others_inputs() -> None:
    agent = FakeAgent(text_chunks=["ok"])
    broker = AgentBroker(agent)
    server = AttachServer(broker, host="127.0.0.1", port=0)
    await server.start()
    try:
        r1, w1 = await asyncio.open_connection("127.0.0.1", server.port)
        r2, w2 = await asyncio.open_connection("127.0.0.1", server.port)
        try:
            await asyncio.sleep(0.05)  # let both clients register
            await _send(w1, {"type": "input", "text": "from-client-1"})
            # Both clients should see the input echo.
            echo1 = await _drain_until(r1, lambda m: m["type"] == "input_echo")
            echo2 = await _drain_until(r2, lambda m: m["type"] == "input_echo")
            assert echo1["text"] == "from-client-1"
            assert echo2["text"] == "from-client-1"
            # And both should see the agent's response text.
            text1 = await _drain_until(r1, lambda m: m["type"] == "text")
            text2 = await _drain_until(r2, lambda m: m["type"] == "text")
            assert text1["text"] == "ok"
            assert text2["text"] == "ok"
        finally:
            w1.close()
            w2.close()
            await w1.wait_closed()
            await w2.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_input_when_busy_returns_busy_error() -> None:
    suspend = asyncio.Event()
    agent = FakeAgent(text_chunks=["first"], suspend_event=suspend)
    broker = AgentBroker(agent)
    server = AttachServer(broker, host="127.0.0.1", port=0)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        try:
            await _send(writer, {"type": "input", "text": "a"})
            await _drain_until(reader, lambda m: m["type"] == "text")  # turn is running
            await _send(writer, {"type": "input", "text": "b"})
            err = await _drain_until(reader, lambda m: m["type"] == "error")
            assert "busy" in err["message"].lower()
            suspend.set()
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()
