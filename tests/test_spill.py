"""Tests for spill_if_large and safe_filename in tools/_helpers.py."""

from __future__ import annotations

from typing import Any

import pytest

from discord_claude_control.tools import _context, _helpers
from discord_claude_control.tools._helpers import safe_filename, spill_if_large


class _FakeChannel:
    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []

    async def send(self, *, content: str = "", file: Any = None) -> None:
        # Drain the BytesIO so we can assert on it.
        data = b""
        filename = ""
        if file is not None:
            data = file.fp.getvalue()
            filename = file.filename
        self.sends.append({"content": content, "data": data, "filename": filename})


@pytest.mark.asyncio
async def test_spill_under_threshold_returns_unchanged() -> None:
    short = "hello world"
    out = await spill_if_large(short, threshold=100, filename="x.txt")
    assert out == short


@pytest.mark.asyncio
async def test_spill_over_threshold_no_channel_truncates() -> None:
    big = "x" * 5000
    token = _context.set_channel(None)
    try:
        out = await spill_if_large(big, threshold=100, filename="x.txt")
    finally:
        _context.reset_channel(token)
    assert "truncated" in out
    assert len(out) < len(big)


@pytest.mark.asyncio
async def test_spill_over_threshold_with_channel_posts_and_returns_head() -> None:
    big = "y" * 5000
    fake = _FakeChannel()
    token = _context.set_channel(fake)
    try:
        out = await spill_if_large(big, threshold=200, filename="big.txt")
    finally:
        _context.reset_channel(token)

    # Discord got the full payload.
    assert len(fake.sends) == 1
    assert fake.sends[0]["filename"] == "big.txt"
    assert fake.sends[0]["data"] == big.encode("utf-8")
    # The model copy is truncated with a pointer to the attachment.
    assert "5,000 bytes" in out
    assert "big.txt" in out
    assert len(out) < len(big)


@pytest.mark.asyncio
async def test_spill_send_failure_falls_back_to_truncate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    big = "z" * 5000

    class _BoomChannel:
        async def send(self, **_: Any) -> None:
            raise RuntimeError("network down")

    token = _context.set_channel(_BoomChannel())
    try:
        out = await spill_if_large(big, threshold=100, filename="x.txt")
    finally:
        _context.reset_channel(token)

    # Must not raise; falls back to plain truncation.
    assert "truncated" in out


def test_safe_filename_strips_unsafe_chars() -> None:
    assert safe_filename("hello world!") == "hello_world.txt"
    assert safe_filename("../etc/passwd") == "etc_passwd.txt"
    assert safe_filename("path:with*stars") == "path_with_stars.txt"


def test_safe_filename_keeps_ext() -> None:
    assert safe_filename("output", ext=".log") == "output.log"
    assert safe_filename("output", ext="log") == "output.log"


def test_safe_filename_empty_falls_back() -> None:
    assert safe_filename("") == "output.txt"
    assert safe_filename("...") == "output.txt"


def test_safe_filename_length_cap() -> None:
    long = "a" * 200
    out = safe_filename(long)
    assert len(out) <= 80 + len(".txt")
