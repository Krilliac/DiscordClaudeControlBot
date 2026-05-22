"""
Connect to a running discord-claude-control attach server.

Usage:
    python -m discord_claude_control.attach
    python -m discord_claude_control.attach --port 9999

Type a message and press Enter to send it as a user turn. Special commands:
    !stop     interrupt the agent
    Ctrl-C    detach (does not stop the agent or the bot)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876


async def _run(host: str, port: int) -> int:
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except OSError as e:
        print(f"connect failed: {e}", file=sys.stderr)
        return 1

    print(f"attached to {host}:{port}")
    print("type a message and press Enter. !stop interrupts. Ctrl-C detaches.")
    print()

    stop = asyncio.Event()

    async def reader_loop() -> None:
        try:
            async for raw in reader:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _render(msg)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            stop.set()

    r_task = asyncio.create_task(reader_loop())

    loop = asyncio.get_running_loop()
    rc = 0
    try:
        while not stop.is_set():
            try:
                line = await asyncio.wait_for(
                    loop.run_in_executor(None, sys.stdin.readline), timeout=0.5
                )
            except TimeoutError:
                continue
            if line == "":
                # EOF on stdin.
                break
            text = line.rstrip("\n")
            if not text:
                continue
            payload: dict[str, Any] = (
                {"type": "interrupt"} if text == "!stop" else {"type": "input", "text": text}
            )
            try:
                writer.write((json.dumps(payload) + "\n").encode("utf-8"))
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                print("\nserver closed connection.", file=sys.stderr)
                rc = 2
                break
    except KeyboardInterrupt:
        print("\ndetaching.")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        r_task.cancel()
    return rc


def _render(msg: dict[str, Any]) -> None:
    kind = msg.get("type")
    if kind == "text":
        print(msg.get("text", ""), end="", flush=True)
    elif kind == "input_echo":
        source = msg.get("source", "?")
        print(f"\n>>> [{source}] {msg.get('text', '')}", flush=True)
    elif kind == "turn_done":
        marker = "(error)" if msg.get("is_error") else "(end_turn)"
        reason = msg.get("stop_reason")
        suffix = f" reason={reason}" if reason else ""
        print(f"\n--- {marker}{suffix} ---", flush=True)
    elif kind == "error":
        print(f"\n!! {msg.get('message', '')}", flush=True)
    elif kind == "pong":
        did = msg.get("did")
        if did:
            print(f"\n[pong: {did}]", flush=True)
        else:
            print("\n[pong]", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="discord-claude-control.attach")
    parser.add_argument("--host", default=DEFAULT_HOST, help="server host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="server port (default 9876)")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args.host, args.port))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
