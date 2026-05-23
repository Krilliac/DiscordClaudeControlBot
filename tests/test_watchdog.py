"""Tests for the external watchdog state machine + heartbeat reader."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from discord_claude_control import watchdog


# --------------------------------------------------------------------- #
# read_heartbeat_age                                                    #
# --------------------------------------------------------------------- #


def test_age_missing_file_returns_none(tmp_path: Path) -> None:
    assert watchdog.read_heartbeat_age(tmp_path / "nope") is None


def test_age_fresh_returns_small_value(tmp_path: Path) -> None:
    p = tmp_path / "heartbeat"
    now = 1_700_000_000.0
    p.write_text(f"{now - 5.0}\n")
    age = watchdog.read_heartbeat_age(p, now=now)
    assert age is not None
    assert abs(age - 5.0) < 0.01


def test_age_old_returns_large_value(tmp_path: Path) -> None:
    p = tmp_path / "heartbeat"
    now = 1_700_000_000.0
    p.write_text(f"{now - 600.0}\n")
    age = watchdog.read_heartbeat_age(p, now=now)
    assert age == pytest.approx(600.0)


def test_age_unparseable_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "heartbeat"
    p.write_text("not a number\n")
    assert watchdog.read_heartbeat_age(p) is None


def test_age_clamped_to_nonnegative(tmp_path: Path) -> None:
    # Clock skew could make the file appear "newer than now".
    p = tmp_path / "heartbeat"
    p.write_text("9999999999\n")
    age = watchdog.read_heartbeat_age(p, now=1.0)
    assert age == 0.0


# --------------------------------------------------------------------- #
# evaluate (decision matrix)                                            #
# --------------------------------------------------------------------- #


def _state(in_alert: bool = False, last_alert_ts: float = 0.0) -> watchdog.WatchdogState:
    return watchdog.WatchdogState(in_alert=in_alert, last_alert_ts=last_alert_ts)


def test_evaluate_ok_when_fresh() -> None:
    d = watchdog.evaluate(
        age=10.0, stale_threshold_s=180.0, cooldown_s=600.0, state=_state(), now=1000.0
    )
    assert d == "ok"


def test_evaluate_alert_when_stale_and_not_alerted() -> None:
    d = watchdog.evaluate(
        age=600.0, stale_threshold_s=180.0, cooldown_s=600.0, state=_state(), now=1000.0
    )
    assert d == "alert"


def test_evaluate_alert_when_missing() -> None:
    d = watchdog.evaluate(
        age=None, stale_threshold_s=180.0, cooldown_s=600.0, state=_state(), now=1000.0
    )
    assert d == "alert"


def test_evaluate_suppress_within_cooldown() -> None:
    d = watchdog.evaluate(
        age=600.0,
        stale_threshold_s=180.0,
        cooldown_s=600.0,
        state=_state(in_alert=True, last_alert_ts=900.0),
        now=1000.0,  # 100s since last alert; cooldown is 600s
    )
    assert d == "suppress"


def test_evaluate_alert_after_cooldown() -> None:
    d = watchdog.evaluate(
        age=600.0,
        stale_threshold_s=180.0,
        cooldown_s=600.0,
        state=_state(in_alert=True, last_alert_ts=100.0),
        now=1000.0,  # 900s since last alert; cooldown is 600s
    )
    assert d == "alert"


def test_evaluate_recover_when_fresh_after_alert() -> None:
    d = watchdog.evaluate(
        age=5.0,
        stale_threshold_s=180.0,
        cooldown_s=600.0,
        state=_state(in_alert=True, last_alert_ts=900.0),
        now=1000.0,
    )
    assert d == "recover"


# --------------------------------------------------------------------- #
# run_watchdog state transitions                                        #
# --------------------------------------------------------------------- #


def test_run_watchdog_alerts_on_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hb = tmp_path / "heartbeat"
    hb.write_text(f"{time.time() - 1000.0}\n")  # very stale

    posts: list[str] = []

    def fake_post(url: str, content: str, **_: Any) -> bool:
        posts.append(content)
        return True

    restarts: list[Path] = []

    def fake_restart(script: Path, **_: Any) -> tuple[bool, str]:
        restarts.append(script)
        return True, "ok"

    monkeypatch.setattr(watchdog, "post_alert", fake_post)
    monkeypatch.setattr(watchdog, "run_restart_script", fake_restart)

    watchdog.run_watchdog(
        heartbeat_path=hb,
        webhook_url="https://example/webhook",
        restart_script=tmp_path / "fake_restart.ps1",
        stale_threshold_s=30.0,
        check_interval_s=0.001,
        cooldown_s=600.0,
        sleep=lambda _: None,
        iterations=1,
    )
    assert restarts, "expected restart_script to be invoked on stale heartbeat"
    assert any("stale" in p for p in posts)
    assert any("restart" in p.lower() for p in posts)


def test_run_watchdog_suppresses_within_cooldown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hb = tmp_path / "heartbeat"
    hb.write_text(f"{time.time() - 1000.0}\n")

    posts: list[str] = []
    monkeypatch.setattr(watchdog, "post_alert", lambda url, c, **_: posts.append(c) or True)
    monkeypatch.setattr(watchdog, "run_restart_script", lambda *_a, **_k: (True, "ok"))

    watchdog.run_watchdog(
        heartbeat_path=hb,
        webhook_url="https://example/webhook",
        restart_script=None,
        stale_threshold_s=30.0,
        check_interval_s=0.001,
        cooldown_s=600.0,
        sleep=lambda _: None,
        iterations=3,  # heartbeat stays stale across iterations
    )
    # First iteration alerts (the "stale" message + maybe a follow-up).
    # Subsequent iterations are suppressed -- so we should NOT see N copies.
    stale_msgs = [p for p in posts if "stale" in p]
    assert len(stale_msgs) == 1, f"expected 1 stale alert across 3 iterations, got {posts}"


def test_run_watchdog_recovers_after_fresh_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hb = tmp_path / "heartbeat"
    # iteration 1: stale  ->  alert
    # iteration 2: fresh  ->  recover
    iteration = {"n": 0}

    def fake_age(path: Path, *, now: float | None = None) -> float | None:
        iteration["n"] += 1
        return 1000.0 if iteration["n"] == 1 else 5.0

    monkeypatch.setattr(watchdog, "read_heartbeat_age", fake_age)
    posts: list[str] = []
    monkeypatch.setattr(watchdog, "post_alert", lambda url, c, **_: posts.append(c) or True)
    monkeypatch.setattr(watchdog, "run_restart_script", lambda *_a, **_k: (True, "ok"))

    watchdog.run_watchdog(
        heartbeat_path=hb,
        webhook_url="https://example/webhook",
        restart_script=None,
        stale_threshold_s=30.0,
        check_interval_s=0.001,
        cooldown_s=600.0,
        sleep=lambda _: None,
        iterations=2,
    )
    assert any("stale" in p for p in posts)
    assert any("recovered" in p for p in posts)


# --------------------------------------------------------------------- #
# run_restart_script                                                    #
# --------------------------------------------------------------------- #


def test_run_restart_script_reports_missing(tmp_path: Path) -> None:
    ok, summary = watchdog.run_restart_script(tmp_path / "nope.ps1")
    assert ok is False
    assert "not found" in summary
