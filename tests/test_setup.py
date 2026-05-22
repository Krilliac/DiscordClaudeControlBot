"""
Tests for the setup wizard. Interactive prompts are exercised via stdin
piping; the wizard's filesystem behavior is verified by reading the
generated .env and config.toml.
"""

from __future__ import annotations

import io
import tomllib
from pathlib import Path

import pytest

from discord_claude_control.config import load_config
from discord_claude_control.setup import (
    _validate_positive_int,
    _validate_snowflake,
    _validate_token,
    main,
)


def _drive(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(answers) + "\n"))


def test_non_interactive_writes_placeholder_files(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    cfg = tmp_path / "config.toml"
    rc = main(
        [
            "--non-interactive",
            "--env-path",
            str(env),
            "--config-path",
            str(cfg),
        ]
    )
    assert rc == 0
    assert env.exists()
    assert cfg.exists()
    assert "DISCORD_BOT_TOKEN=" in env.read_text()
    parsed = tomllib.loads(cfg.read_text())
    assert parsed["discord"]["allowed_user_id"] == 0
    assert parsed["attach"]["enabled"] is False


def test_refuses_to_overwrite(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    cfg = tmp_path / "config.toml"
    env.write_text("existing")
    rc = main(["--non-interactive", "--env-path", str(env), "--config-path", str(cfg)])
    assert rc == 1
    # Original file untouched.
    assert env.read_text() == "existing"


def test_force_overwrites(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    cfg = tmp_path / "config.toml"
    env.write_text("old")
    rc = main(
        [
            "--non-interactive",
            "--force",
            "--env-path",
            str(env),
            "--config-path",
            str(cfg),
        ]
    )
    assert rc == 0
    assert "DISCORD_BOT_TOKEN=" in env.read_text()


def test_interactive_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    cfg = tmp_path / "config.toml"
    # Order matches the prompts: bot token, user id, channel id, guild id,
    # api key (blank), model (Enter for default), idle timeout (Enter), attach y.
    _drive(
        monkeypatch,
        [
            "x" * 50,
            "111111111111111111",
            "222222222222222222",
            "333333333333333333",
            "",
            "",
            "",
            "y",
        ],
    )
    rc = main(["--env-path", str(env), "--config-path", str(cfg)])
    assert rc == 0
    env_text = env.read_text()
    assert "DISCORD_BOT_TOKEN=" + "x" * 50 in env_text
    assert "# ANTHROPIC_API_KEY=" in env_text  # blank -> commented
    parsed = tomllib.loads(cfg.read_text())
    assert parsed["discord"]["allowed_user_id"] == 111111111111111111
    assert parsed["discord"]["allowed_channel_id"] == 222222222222222222
    assert parsed["discord"]["allowed_guild_id"] == 333333333333333333
    assert parsed["agent"]["model"] == "claude-opus-4-7"
    assert parsed["session"]["idle_timeout_minutes"] == 10
    assert parsed["attach"]["enabled"] is True
    # And the file load_config-parses cleanly.
    loaded = load_config(cfg)
    assert loaded.discord.allowed_user_id == 111111111111111111
    assert loaded.attach.enabled is True


def test_interactive_with_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    cfg = tmp_path / "config.toml"
    _drive(
        monkeypatch,
        [
            "x" * 60,
            "111111111111111111",
            "222222222222222222",
            "333333333333333333",
            "sk-ant-test-key",
            "",
            "",
            "n",
        ],
    )
    rc = main(["--env-path", str(env), "--config-path", str(cfg)])
    assert rc == 0
    env_text = env.read_text()
    assert "ANTHROPIC_API_KEY=sk-ant-test-key" in env_text
    assert "# ANTHROPIC_API_KEY" not in env_text
    parsed = tomllib.loads(cfg.read_text())
    assert parsed["attach"]["enabled"] is False


def test_validate_token() -> None:
    _validate_token("x" * 50)
    with pytest.raises(ValueError, match=r"too short|chars"):
        _validate_token("short")
    with pytest.raises(ValueError, match=r"spaces"):
        _validate_token("x" * 30 + " spaces are not allowed")


def test_validate_snowflake() -> None:
    _validate_snowflake("123456789012345678")
    with pytest.raises(ValueError, match="numeric"):
        _validate_snowflake("not-a-number")
    with pytest.raises(ValueError, match="17"):
        _validate_snowflake("123")


def test_validate_positive_int() -> None:
    _validate_positive_int("10")
    with pytest.raises(ValueError):
        _validate_positive_int("0")
    with pytest.raises(ValueError):
        _validate_positive_int("-5")
    with pytest.raises(ValueError):
        _validate_positive_int("abc")
