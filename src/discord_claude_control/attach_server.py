"""
Local-attach server. Lets a user-side CLI (`python -m discord_claude_control
.attach`) drive the same agent conversation Discord is driving. Loopback
only by default; do not bind to a public interface unless you've added
your own authentication on top.

Wire format: newline-delimited JSON, one event per line.

Client -> server:
    {"type": "input", "text": "..."}
    {"type": "interrupt"}
    {"type": "ping"}

Server -> client (broadcast to ALL connected clients):
    {"type": "input_echo", "source": "discord|attach", "text": "..."}
    {"type": "text", "text": "..."}
    {"type": "turn_done", "is_error": bool, "stop_reason": str | null}
    {"type": "error", "message": "..."}
    {"type": "pong"}

All connected attach clients see all events, including inputs that came
from Discord -- so an attach session is a faithful mirror of the entire
conversation, not a private side-channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .agent import TurnResult
from .broker import AgentBroker

log = logging.getLogger(__name__)


@dataclass(eq=False)
class _Client:
    writer: asyncio.StreamWriter
    peer: str
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=512))


class AttachServer:
    def __init__(self, broker: AgentBroker, host: str, port: int) -> None:
        self._broker = broker
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None
        self._clients: set[_Client] = set()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._sink = _AttachSink(self)
        # Mirror Discord (and other-attach) inputs to all attach clients.
        self._broker.add_input_listener(self._on_input)

    @property
    def port(self) -> int:
        return self._port

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self._host, self._port)
        if self._server.sockets:
            self._port = self._server.sockets[0].getsockname()[1]
        log.info("attach server listening on %s:%d", self._host, self._port)
        self._broker.add_sink(self._sink)

    async def stop(self) -> None:
        self._broker.remove_sink(self._sink)
        for client in list(self._clients):
            with contextlib.suppress(Exception):
                client.writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def broadcast(self, msg: dict[str, Any]) -> None:
        dropped: list[_Client] = []
        for client in list(self._clients):
            try:
                client.queue.put_nowait(msg)
            except asyncio.QueueFull:
                log.warning("attach client %s queue full; dropping", client.peer)
                dropped.append(client)
        for client in dropped:
            self._clients.discard(client)
            with contextlib.suppress(Exception):
                client.writer.close()

    def _on_input(self, source: str, text: str) -> None:
        # Called synchronously from the broker; schedule the broadcast and
        # keep a reference so the task isn't garbage-collected mid-flight.
        task = asyncio.create_task(
            self.broadcast({"type": "input_echo", "source": source, "text": text})
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = str(writer.get_extra_info("peername"))
        client = _Client(writer=writer, peer=peer)
        self._clients.add(client)
        log.info("attach client connected from %s (total=%d)", peer, len(self._clients))

        sender = asyncio.create_task(self._sender_loop(client))
        try:
            async for raw in reader:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    await client.queue.put({"type": "error", "message": "invalid json"})
                    continue
                if not isinstance(msg, dict):
                    await client.queue.put({"type": "error", "message": "not a json object"})
                    continue
                await self._dispatch(msg, client)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            self._clients.discard(client)
            sender.cancel()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            log.info("attach client %s disconnected (remaining=%d)", peer, len(self._clients))

    async def _sender_loop(self, client: _Client) -> None:
        try:
            while True:
                msg = await client.queue.get()
                line = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
                try:
                    client.writer.write(line)
                    await client.writer.drain()
                except (ConnectionResetError, BrokenPipeError):
                    return
        except asyncio.CancelledError:
            raise

    async def _dispatch(self, msg: dict[str, Any], client: _Client) -> None:
        kind = msg.get("type")
        if kind == "input":
            text = msg.get("text", "")
            if not isinstance(text, str) or not text.strip():
                await client.queue.put({"type": "error", "message": "input.text required"})
                return
            ok = self._broker.submit_nowait(text, "attach")
            if not ok:
                await client.queue.put(
                    {"type": "error", "message": "busy: previous turn still running"}
                )
        elif kind == "interrupt":
            await self._broker.interrupt()
            await client.queue.put({"type": "pong", "did": "interrupt"})
        elif kind == "ping":
            await client.queue.put({"type": "pong"})
        else:
            await client.queue.put({"type": "error", "message": f"unknown type {kind!r}"})


class _AttachSink:
    """ResponseSink that fans out to every connected attach client."""

    def __init__(self, server: AttachServer) -> None:
        self._server = server

    async def append(self, text: str) -> None:
        await self._server.broadcast({"type": "text", "text": text})

    async def commit(self, result: TurnResult) -> None:
        await self._server.broadcast(
            {
                "type": "turn_done",
                "is_error": result.is_error,
                "stop_reason": result.stop_reason,
            }
        )

    async def fail(self, error: BaseException) -> None:
        await self._server.broadcast(
            {"type": "error", "message": f"{type(error).__name__}: {error}"}
        )
