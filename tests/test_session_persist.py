from __future__ import annotations

from pathlib import Path

import pytest

from discord_claude_control.session_persist import SessionIdStore


def test_load_returns_none_on_fresh_db(tmp_path: Path) -> None:
    store = SessionIdStore(tmp_path / "sessions.db")
    assert store.load() is None


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    store = SessionIdStore(tmp_path / "sessions.db")
    store.save("session-42")
    assert store.load() == "session-42"


def test_save_overwrites_previous(tmp_path: Path) -> None:
    store = SessionIdStore(tmp_path / "sessions.db")
    store.save("first")
    store.save("second")
    assert store.load() == "second"


def test_separate_instances_share_state(tmp_path: Path) -> None:
    db = tmp_path / "sessions.db"
    SessionIdStore(db).save("durable")
    assert SessionIdStore(db).load() == "durable"


def test_clear_returns_none(tmp_path: Path) -> None:
    store = SessionIdStore(tmp_path / "sessions.db")
    store.save("x")
    store.clear()
    assert store.load() is None


def test_save_empty_string_rejected(tmp_path: Path) -> None:
    store = SessionIdStore(tmp_path / "sessions.db")
    with pytest.raises(ValueError):
        store.save("")


def test_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "deeper" / "sessions.db"
    SessionIdStore(nested)
    assert nested.parent.exists()
