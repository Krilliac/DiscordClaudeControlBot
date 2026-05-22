from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from discord_claude_control.audit import (
    _LOGGER_NAME,
    audit_wrap,
    log_invocation,
    setup_audit_logger,
)


@pytest.fixture(autouse=True)
def _isolated_logger() -> Iterator[None]:
    logger = logging.getLogger(_LOGGER_NAME)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    yield
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)


def _read_log(p: Path) -> list[dict[str, Any]]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]


def test_setup_creates_file_and_writes_one_line(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    setup_audit_logger(log_path)
    log_invocation(
        "run_powershell",
        {"command": "Get-Service"},
        {"content": [{"type": "text", "text": "ok"}]},
    )
    entries = _read_log(log_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["tool"] == "run_powershell"
    assert entry["args"] == {"command": "Get-Service"}
    assert entry["is_error"] is False
    assert entry["result"] == "ok"
    assert "ts" in entry


def test_records_is_error_flag(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    setup_audit_logger(log_path)
    log_invocation(
        "read_file",
        {"path": "/etc/shadow"},
        {"content": [{"type": "text", "text": "ERROR: nope"}], "is_error": True},
    )
    entries = _read_log(log_path)
    assert entries[0]["is_error"] is True


def test_long_args_are_truncated(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    setup_audit_logger(log_path)
    long_value = "x" * 500
    log_invocation(
        "write_file",
        {"path": "/tmp/foo", "content": long_value},
        {"content": [{"type": "text", "text": "wrote 500 chars"}]},
    )
    entries = _read_log(log_path)
    args = entries[0]["args"]
    assert len(args["content"]) <= 300  # 200 + truncation marker
    assert "more chars" in args["content"]


def test_setup_is_idempotent(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    setup_audit_logger(log_path)
    setup_audit_logger(log_path)
    setup_audit_logger(log_path)
    log_invocation("x", {}, {"content": [{"type": "text", "text": "ok"}]})
    entries = _read_log(log_path)
    # If setup added handlers each call, the line would be duplicated.
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_audit_wrap_logs_after_call(tmp_path: Path) -> None:
    setup_audit_logger(tmp_path / "audit.log")
    called_with: list[dict[str, Any]] = []

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        called_with.append(args)
        return {"content": [{"type": "text", "text": "ran"}]}

    wrapped = audit_wrap("my_tool", handler)
    result = await wrapped({"a": 1})
    assert result == {"content": [{"type": "text", "text": "ran"}]}
    assert called_with == [{"a": 1}]
    entries = _read_log(tmp_path / "audit.log")
    assert entries[0]["tool"] == "my_tool"
    assert entries[0]["args"] == {"a": 1}
    assert entries[0]["result"] == "ran"
