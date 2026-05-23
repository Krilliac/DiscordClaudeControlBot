"""Tests for the per-turn usage telemetry."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from discord_claude_control import usage


def test_record_writes_jsonl(tmp_path: Path) -> None:
    p = tmp_path / "usage.log"
    usage.record_turn(
        p,
        model="claude-opus-4-7",
        usage_dict={"input_tokens": 100, "output_tokens": 50},
        total_cost_usd=0.0123,
        duration_ms=4321,
        session_id="sess-1",
        ts=1700000000.0,
    )
    lines = p.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["model"] == "claude-opus-4-7"
    assert entry["input_tokens"] == 100
    assert entry["output_tokens"] == 50
    assert entry["total_cost_usd"] == 0.0123
    assert entry["duration_ms"] == 4321
    assert entry["session_id"] == "sess-1"
    assert entry["ts"] == 1700000000.0


def test_record_handles_none_usage(tmp_path: Path) -> None:
    p = tmp_path / "usage.log"
    usage.record_turn(
        p,
        model="m",
        usage_dict=None,
        total_cost_usd=None,
        duration_ms=0,
        session_id="s",
        ts=1.0,
    )
    entry = json.loads(p.read_text().splitlines()[0])
    assert entry["input_tokens"] == 0
    assert entry["output_tokens"] == 0
    assert entry["total_cost_usd"] is None


def test_record_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "deep" / "nested" / "usage.log"
    usage.record_turn(
        p, model="m", usage_dict={}, total_cost_usd=0.0, duration_ms=0, session_id="s", ts=1.0
    )
    assert p.exists()


def test_record_swallows_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "usage.log"

    def boom(*_a: object, **_k: object) -> object:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", boom)  # type: ignore[arg-type]
    # Must not raise.
    usage.record_turn(
        p, model="m", usage_dict={}, total_cost_usd=0.0, duration_ms=0, session_id="s"
    )


def test_parse_period_defaults_to_today() -> None:
    assert usage.parse_period("") == "today"
    assert usage.parse_period("   ") == "today"


def test_parse_period_known_values() -> None:
    for p in ("today", "week", "month", "all"):
        assert usage.parse_period(p) == p
        assert usage.parse_period(p.upper()) == p
        assert usage.parse_period(f"  {p}  ") == p


def test_parse_period_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown period"):
        usage.parse_period("yesterday")


def test_summarize_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "missing.log"
    assert "no turns logged" in usage.summarize(p, "today")


def test_summarize_period_filtering(tmp_path: Path) -> None:
    p = tmp_path / "usage.log"
    now = time.time()
    # Write three entries: today, 3 days ago, 40 days ago.
    today_midnight = datetime.fromtimestamp(now).replace(
        hour=12, minute=0, second=0, microsecond=0
    ).timestamp()
    three_days_ago = now - 3 * 86400
    forty_days_ago = now - 40 * 86400
    for ts in (today_midnight, three_days_ago, forty_days_ago):
        usage.record_turn(
            p,
            model="m",
            usage_dict={"input_tokens": 1000, "output_tokens": 200},
            total_cost_usd=0.01,
            duration_ms=100,
            session_id="s",
            ts=ts,
        )

    today = usage.summarize(p, "today", now=now)
    assert "turns:  1" in today

    week = usage.summarize(p, "week", now=now)
    assert "turns:  2" in week

    month = usage.summarize(p, "month", now=now)
    assert "turns:  2" in month  # 40 days is outside 30-day window

    all_ = usage.summarize(p, "all", now=now)
    assert "turns:  3" in all_


def test_summarize_subscription_mode(tmp_path: Path) -> None:
    p = tmp_path / "usage.log"
    usage.record_turn(
        p,
        model="claude-opus-4-7",
        usage_dict={"input_tokens": 100, "output_tokens": 50},
        total_cost_usd=0.0,  # subscription mode -- SDK reports $0
        duration_ms=100,
        session_id="s",
        ts=time.time(),
    )
    out = usage.summarize(p, "today")
    assert "subscription mode" in out
    assert "$0.0000" not in out  # we suppress the line when no $ was spent


def test_summarize_skips_malformed_lines(tmp_path: Path) -> None:
    p = tmp_path / "usage.log"
    now = time.time()
    p.write_text(
        "\n"
        "{not json\n"
        "[not a dict]\n"
        + json.dumps(
            {
                "ts": now,
                "model": "m",
                "input_tokens": 5,
                "output_tokens": 6,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "total_cost_usd": 0.01,
                "duration_ms": 1,
                "session_id": "s",
            }
        )
        + "\n"
    )
    out = usage.summarize(p, "today", now=now)
    assert "turns:  1" in out


def test_summarize_per_model_breakdown(tmp_path: Path) -> None:
    p = tmp_path / "usage.log"
    now = time.time()
    for model in ("opus", "sonnet"):
        usage.record_turn(
            p,
            model=model,
            usage_dict={"input_tokens": 100, "output_tokens": 50},
            total_cost_usd=0.01,
            duration_ms=100,
            session_id="s",
            ts=now,
        )
    out = usage.summarize(p, "today", now=now)
    assert "by model" in out
    assert "opus" in out
    assert "sonnet" in out
