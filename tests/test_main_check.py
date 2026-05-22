"""Tests for the --check preflight flag on the main entry point."""

from __future__ import annotations

from pathlib import Path

import pytest

from discord_claude_control.main import main


def _write_config(path: Path) -> None:
    path.write_text(
        """
[discord]
allowed_user_id    = 1
allowed_channel_id = 2
allowed_guild_id   = 3

[agent]
model = "claude-opus-4-7"
max_tool_calls_per_message = 20
conversation_db_path = "conversation.db"

[session]
idle_timeout_minutes = 10

[tools]
input_auth_mode = "autonomous"
restrict_paths = false
allow_roots    = []
enabled        = []

[logging]
audit_log_path = "audit.log"
audit_max_bytes = 5242880
audit_backup_count = 5
level = "INFO"

[attach]
enabled = false
host    = "127.0.0.1"
port    = 9876
""".strip(),
        encoding="utf-8",
    )


def _isolate_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out python-dotenv so the repo's real .env doesn't leak into tests.

    ``load_secrets`` calls ``load_dotenv()`` which walks parent directories
    looking for a .env. The bot's repo root has a real .env with a real
    DISCORD_BOT_TOKEN; without this stub, ``monkeypatch.delenv`` is undone
    by ``load_dotenv`` rediscovering the token.
    """
    monkeypatch.setattr("discord_claude_control.config.load_dotenv", lambda *a, **kw: False)


def test_check_returns_zero_on_good_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = tmp_path / "config.toml"
    _write_config(cfg)
    _isolate_dotenv(monkeypatch)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake-token-for-tests")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    rc = main(["--config", str(cfg), "--check"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "preflight OK" in out
    assert "subscription mode" in out
    assert "claude-opus-4-7" in out


def test_check_missing_config_returns_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _isolate_dotenv(monkeypatch)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.chdir(tmp_path)
    rc = main(["--config", str(tmp_path / "nope.toml"), "--check"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "preflight" in err
    assert "nope.toml" in err


def test_check_bad_config_returns_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[discord]
allowed_user_id    = -5
allowed_channel_id = 2
allowed_guild_id   = 3
""".strip(),
        encoding="utf-8",
    )
    _isolate_dotenv(monkeypatch)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.chdir(tmp_path)
    rc = main(["--config", str(cfg), "--check"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "preflight" in err
    assert "allowed_user_id" in err


def test_check_missing_secrets_returns_3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = tmp_path / "config.toml"
    _write_config(cfg)
    _isolate_dotenv(monkeypatch)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    rc = main(["--config", str(cfg), "--check"])
    err = capsys.readouterr().err
    assert rc == 3
    assert "secrets" in err
    assert "DISCORD_BOT_TOKEN" in err


def test_check_api_mode_reported_when_key_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = tmp_path / "config.toml"
    _write_config(cfg)
    _isolate_dotenv(monkeypatch)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    monkeypatch.chdir(tmp_path)
    rc = main(["--config", str(cfg), "--check"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "API mode" in out
